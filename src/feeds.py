"""
Finds, fetches and parses the RSS or Atom feeds of the sources and selects
the posts for a run.

All network access goes through a fetcher function, so that tests can replace it.
"""
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlsplit

import feedparser
import requests

from settings import Settings
from state import PipelineState
from urls import normalize_url

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_S = 20
# Tried in this order after the linked feeds: WordPress, Blogger, Squarespace, Wix and static sites.
# Squarespace homepages often link the feed of a static page, so the blog paths are needed.
FALLBACK_FEED_PATHS = ("/feed/", "/feeds/posts/default", "/blog?format=rss", "/?format=rss", "/blog/feed/",
                       "/journal?format=rss", "/rss", "/blog-feed.xml", "/atom.xml", "/index.xml")
# Homepage status codes that mean the site refuses automated requests
BLOCKED_STATUSES = frozenset({401, 403})
# Posts whose title or tags contain one of these words are not sent to the model
NON_FASHION_PATTERN = re.compile(
    r"\b(recipes?|books?|reading|podcasts?|interiors?|decor|giveaways?|skincare|gift guides?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HttpResponse:
    """The parts of an HTTP response the pipeline uses.

    Attributes:
        url (str): Final URL after redirects.
        status (int): HTTP status code.
        content (bytes): Response body.
        content_type (str): Value of the Content-Type header.
    """
    url: str
    status: int
    content: bytes
    content_type: str = ""


Fetcher = Callable[[str], HttpResponse]


def http_get(url: str) -> HttpResponse:
    """Fetches a URL with the pipeline's user agent and timeout.

    Args:
        url: The URL.

    Returns:
        HttpResponse: The response, also for error status codes.

    Raises:
        requests.RequestException: For network errors and timeouts.
    """
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_S)
    return HttpResponse(
        url=response.url,
        status=response.status_code,
        content=response.content,
        content_type=response.headers.get("Content-Type", ""),
    )


class FeedError(Exception):
    """A feed could not be fetched or parsed."""


@dataclass(frozen=True)
class FeedPost:
    """One entry of a feed.

    Attributes:
        url (str): Absolute URL of the post.
        title (Optional[str]): Title of the post.
        published (Optional[date]): Publish date, or the update date if the feed has no publish date.
        tags (tuple[str, ...]): Categories and tags.
        content_html (Optional[str]): Full HTML content, if the feed includes it.
    """
    url: str
    title: Optional[str]
    published: Optional[date]
    tags: tuple[str, ...] = ()
    content_html: Optional[str] = None


@dataclass(frozen=True)
class ParsedFeed:
    """A parsed feed.

    Attributes:
        url (str): Final URL of the feed.
        posts (tuple[FeedPost, ...]): The entries with a usable link.
        language (Optional[str]): Language tag of the channel, e.g. ``en-GB``.
        newest_post (Optional[date]): Date of the newest entry.
    """
    url: str
    posts: tuple[FeedPost, ...]
    language: Optional[str]
    newest_post: Optional[date]


def _feed_post(entry: feedparser.FeedParserDict, feed_url: str) -> Optional[FeedPost]:
    """Converts a feedparser entry.

    Args:
        entry: The entry.
        feed_url: URL of the feed, used to resolve relative links.

    Returns:
        Optional[FeedPost]: The post, or None if the entry has no http(s) link.
    """
    link = (entry.get("link") or "").strip()
    url = urljoin(feed_url, link) if link else ""
    if not url.startswith(("http://", "https://")):
        return None
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    tags = (" ".join((tag.get("term") or "").split()) for tag in entry.get("tags", []))
    content_html = next(
        (item.get("value") for item in entry.get("content", [])
         if item.get("value") and "html" in (item.get("type") or "")),
        None,
    )
    return FeedPost(
        url=url,
        title=" ".join((entry.get("title") or "").split()) or None,
        published=date(stamp.tm_year, stamp.tm_mon, stamp.tm_mday) if stamp else None,
        tags=tuple(dict.fromkeys(tag for tag in tags if tag)),
        content_html=content_html,
    )


def parse_feed(content: bytes, url: str) -> Optional[ParsedFeed]:
    """Parses RSS or Atom.

    HTML sanitizing is off, so that image attributes such as ``srcset`` and
    ``data-src`` in the content survive. The HTML is only parsed, never rendered.

    Args:
        content: The response body.
        url: Final URL of the response.

    Returns:
        Optional[ParsedFeed]: The feed, or None if the content is not a feed.
    """
    parsed = feedparser.parse(content, sanitize_html=False, response_headers={"content-location": url})
    if not parsed.get("version"):
        return None
    posts = tuple(post for entry in parsed.entries if (post := _feed_post(entry, url)) is not None)
    dates = [post.published for post in posts if post.published]
    return ParsedFeed(
        url=url,
        posts=posts,
        language=(parsed.feed.get("language") or "").strip() or None,
        newest_post=max(dates, default=None),
    )


def fetch_feed(feed_url: str, fetch: Fetcher = http_get) -> ParsedFeed:
    """Fetches and parses a feed.

    Args:
        feed_url: URL of the feed.
        fetch: Fetcher function.

    Returns:
        ParsedFeed: The parsed feed.

    Raises:
        FeedError: For network errors, status codes other than 200 and content that is not a feed.
    """
    try:
        response = fetch(feed_url)
    except requests.RequestException as e:
        raise FeedError(f"{feed_url}: {type(e).__name__}") from e
    if response.status != 200:
        raise FeedError(f"{feed_url}: HTTP {response.status}")
    parsed = parse_feed(response.content, response.url)
    if parsed is None:
        raise FeedError(f"{feed_url}: not a feed")
    return parsed


class _FeedLinkParser(HTMLParser):
    """Collects ``<link rel="alternate">`` elements that point to RSS or Atom feeds."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        """Records feed links; other tags are ignored."""
        if tag != "link":
            return
        values = {name.lower(): (value or "") for name, value in attrs}
        rel = values.get("rel", "").lower().split()
        kind = values.get("type", "").lower()
        if "alternate" in rel and kind in ("application/rss+xml", "application/atom+xml") and values.get("href"):
            self.links.append((values["href"], values.get("title", "")))


def feed_links_from_html(content: bytes, base_url: str) -> list[str]:
    """Returns the post feeds that an HTML page links, without comment feeds.

    Args:
        content: The HTML.
        base_url: URL of the page, used to resolve relative links.

    Returns:
        list[str]: Absolute feed URLs in page order.
    """
    parser = _FeedLinkParser()
    parser.feed(content.decode("utf-8", errors="replace"))
    return [
        urljoin(base_url, href) for href, title in parser.links
        if "comment" not in href.lower() and "comment" not in title.lower()
    ]


def _looks_like_xml(response: HttpResponse) -> bool:
    """Returns True if a response is probably XML rather than HTML."""
    kind = response.content_type.lower()
    return "xml" in kind or "rss" in kind or "atom" in kind or response.content.lstrip()[:5] == b"<?xml"


@dataclass(frozen=True)
class FeedSearch:
    """Result of looking for a blog's feed.

    Attributes:
        feed (Optional[ParsedFeed]): The feed, or None if none was found.
        homepage_status (Optional[int]): HTTP status of the homepage, or None
            if it was not reachable.
    """
    feed: Optional[ParsedFeed]
    homepage_status: Optional[int]


def _has_dated_posts(feed: ParsedFeed) -> bool:
    """Returns True if at least one entry of a feed has a date."""
    return any(post.published for post in feed.posts)


def find_feed(homepage: str, fetch: Fetcher = http_get) -> FeedSearch:
    """Finds the post feed of a blog.

    Tries the feeds linked from the homepage first, then common feed paths. A
    feed without dated entries, such as the feed of a static page, is used
    only if no other feed is found. Sites that block the homepage are not probed further.

    Args:
        homepage: URL of the blog's homepage.
        fetch: Fetcher function.

    Returns:
        FeedSearch: The feed, if any, and the status of the homepage.
    """
    candidates: list[str] = []
    base = homepage
    fallback: Optional[ParsedFeed] = None
    try:
        page = fetch(homepage)
    except requests.RequestException as e:
        logger.info("Homepage %s not reachable (%s); trying common feed paths.", homepage, type(e).__name__)
        page = None
    status = page.status if page is not None else None
    if status in BLOCKED_STATUSES:
        return FeedSearch(None, status)
    if page is not None and status == 200:
        base = page.url
        if _looks_like_xml(page):
            fallback = parse_feed(page.content, page.url)
            if fallback is not None and _has_dated_posts(fallback):
                return FeedSearch(fallback, status)
        candidates.extend(feed_links_from_html(page.content, page.url))
    parts = urlsplit(base)
    root = f"{parts.scheme}://{parts.netloc}/"
    candidates.extend(urljoin(root, path) for path in FALLBACK_FEED_PATHS)
    for url in dict.fromkeys(candidates):
        try:
            feed = fetch_feed(url, fetch)
        except FeedError:
            continue
        if _has_dated_posts(feed):
            return FeedSearch(feed, status)
        fallback = fallback or feed
    return FeedSearch(fallback, status)


def is_non_fashion(title: Optional[str], tags: Sequence[str]) -> bool:
    """Returns True if the title or a tag marks a post as clearly not about outfits.

    Args:
        title: Title of the post.
        tags: Categories and tags of the post.

    Returns:
        bool: True for posts such as book lists, recipes or interior posts.
    """
    return any(NON_FASHION_PATTERN.search(text) for text in (title or "", *tags))


def count_recent_posts(feed: ParsedFeed, today: date, days: int) -> int:
    """Counts the posts published within the last ``days`` days.

    Args:
        feed: The parsed feed.
        today: Reference date.
        days: Length of the window.

    Returns:
        int: Number of posts.
    """
    cutoff = today - timedelta(days=days)
    return sum(1 for post in feed.posts if post.published and post.published >= cutoff)


@dataclass(frozen=True)
class SourceFeed:
    """The fetched feed of a source, as input for the post selection.

    Attributes:
        source_id (str): The source.
        probation (bool): Whether the source is on probation.
        posts (tuple[FeedPost, ...]): The entries of its feed.
    """
    source_id: str
    probation: bool
    posts: tuple[FeedPost, ...]


@dataclass(frozen=True)
class SelectedPost:
    """A post selected for processing in this run.

    Attributes:
        source_id (str): The source.
        key (str): Normalized URL, the key in the post state.
        url (str): URL of the post.
        title (Optional[str]): Title of the post.
        published (Optional[date]): Publish date.
        tags (tuple[str, ...]): Categories and tags.
        content_html (Optional[str]): Full HTML from the feed, if available.
        retry (bool): Whether an earlier attempt failed.
    """
    source_id: str
    key: str
    url: str
    title: Optional[str]
    published: Optional[date]
    tags: tuple[str, ...]
    content_html: Optional[str]
    retry: bool


def _candidates_for_source(
    feed: SourceFeed, state: PipelineState, settings: Settings, today: date
) -> list[SelectedPost]:
    """Returns the due posts of one source, oldest first.

    Args:
        feed: The fetched feed of the source.
        state: The post state.
        settings: The run settings.
        today: Date of the run.

    Returns:
        list[SelectedPost]: New posts from the feed and pending retries.
    """
    cutoff = today - timedelta(days=settings.feed_lookback_days)
    candidates: dict[str, SelectedPost] = {}
    for post in feed.posts:
        if post.published is not None and post.published < cutoff:
            continue
        if is_non_fashion(post.title, post.tags):
            continue
        key = normalize_url(post.url)
        if key in candidates or not state.is_due(key, today, settings.feed_lookback_days):
            continue
        candidates[key] = SelectedPost(
            source_id=feed.source_id, key=key, url=post.url, title=post.title, published=post.published,
            tags=post.tags, content_html=post.content_html, retry=state.get(key) is not None,
        )
    for key, entry in state.pending_for_source(feed.source_id, today, settings.feed_lookback_days):
        if key not in candidates:
            candidates[key] = SelectedPost(
                source_id=feed.source_id, key=key, url=entry.url, title=entry.title, published=entry.published,
                tags=tuple(entry.tags), content_html=None, retry=True,
            )
    return sorted(candidates.values(), key=lambda post: (post.published or today, post.key))


def select_posts(
    feeds: Sequence[SourceFeed], state: PipelineState, settings: Settings, today: date
) -> list[SelectedPost]:
    """Selects the posts of a run.

    Each source contributes at most ``max_posts_per_source`` posts, oldest
    first. The sources take turns: first the oldest post of every source, then
    the second, and so on, until ``max_posts_per_run`` is reached. Sources on
    probation contribute at most ``max_probation_posts_per_run`` posts.

    Args:
        feeds: The fetched feeds of the active and probation sources.
        state: The post state.
        settings: The run settings.
        today: Date of the run.

    Returns:
        list[SelectedPost]: The posts in processing order.
    """
    per_source = [
        (feed, _candidates_for_source(feed, state, settings, today)[: settings.max_posts_per_source])
        for feed in feeds
    ]
    selected: list[SelectedPost] = []
    probation_count = 0
    for round_index in range(settings.max_posts_per_source):
        batch = [(feed, posts[round_index]) for feed, posts in per_source if len(posts) > round_index]
        batch.sort(key=lambda item: (item[1].published or today, item[1].source_id))
        for feed, post in batch:
            if len(selected) >= settings.max_posts_per_run:
                return selected
            if feed.probation:
                if probation_count >= settings.max_probation_posts_per_run:
                    continue
                probation_count += 1
            selected.append(post)
    return selected
