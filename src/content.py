"""
Extracts the article of a post: text, image candidates, external links and the
canonical URL.

crawl4ai builds its image and link lists from the whole page, including the
header, sidebar and related posts, so the article element is selected here.
The functions work on HTML only and are used for crawled pages and for the
full-text content of feeds alike.
"""
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from extraction import MAX_TEXT_CHARS
from urls import domain_id

# Tried one at a time, in this order, because a comma-separated selector would
# return the first match in page order instead of the first selector that matches
ARTICLE_SELECTORS = (".entry-content", ".post-content", ".post-body", "article", "main")
MIN_ARTICLE_TEXT_CHARS = 200
# Elements inside the article that hold navigation, sharing buttons, comments or related posts
NOISE_SELECTORS = ("script", "style", "template", "form", "nav", "aside", "iframe", "[class*=related]",
                   "[id*=related]", "[class*=share]", "[class*=social]", "[class*=comment]", "[id*=comment]",
                   "[class*=newsletter]", "[class*=subscribe]")
# Containers of product carousels and shop-the-look widgets
SHOP_WIDGET_PATTERN = re.compile(
    r"shop-?the-?(post|look)|shopthepost|ltk|rstyle|shopstyle|shopmy|product-(grid|carousel|slider|widget)|affiliate",
    re.IGNORECASE,
)
TARGET_IMAGE_WIDTH = 1200
SRC_ATTRIBUTES = ("data-lazy-src", "data-src", "data-original", "data-orig-file", "src")
SRCSET_ATTRIBUTES = ("data-lazy-srcset", "data-srcset", "srcset")
IMAGE_LINK_PATTERN = re.compile(r"\.(jpe?g|png|webp|avif)(\?|$)", re.IGNORECASE)
IMAGE_LINK_HOSTS = ("blogger.googleusercontent.com", "bp.blogspot.com")
WORDPRESS_UPLOAD_PATTERN = re.compile(r"/wp-content/uploads/(\d{4})/(\d{2})/")
# A post is republished if most of its images were uploaded more than this many months before the publish date
REPUBLISHED_AFTER_MONTHS = 12

# Affiliate networks and frequent retailers; links to them count as shopping links
SHOP_DOMAINS = frozenset({
    "rstyle.me", "shopstyle.it", "shopstyle.com", "liketk.it", "shopltk.com", "liketoknow.it", "shopmy.us",
    "amzn.to", "amzlink.to", "geni.us", "amazon.com", "amazon.co.uk", "amazon.de", "go.skimresources.com",
    "howl.me", "howl.link", "narrativ.com", "bam-x.com", "go.magik.ly", "fave.co", "mavely.app.link",
    "click.linksynergy.com", "anrdoezrs.net", "jdoqocy.com", "tkqlhce.com", "dpbolvw.net", "kqzyfj.com",
    "awin1.com", "shareasale.com", "pntra.com", "sovrn.co", "linksynergy.com",
    # Impact affiliate network, e.g. boden-uk.sjv.io
    "sjv.io", "pxf.io", "7eer.net", "ojrq.net", "evyy.net", "vzew.net", "mkr3.net",
    "nordstrom.com", "target.com",
    "walmart.com", "jcrew.com", "madewell.com", "zara.com", "hm.com", "asos.com", "revolve.com", "shopbop.com",
    "net-a-porter.com", "abercrombie.com", "everlane.com", "anthropologie.com", "freepeople.com", "gap.com",
    "oldnavy.com", "bananarepublic.com", "loft.com", "anntaylor.com", "boden.com", "johnlewis.com",
    "marksandspencer.com", "mango.com", "uniqlo.com", "maurices.com", "etsy.com", "poshmark.com", "thredup.com",
    "ebay.com", "farfetch.com", "ssense.com", "mytheresa.com", "macys.com", "bloomingdales.com",
    "saksfifthavenue.com", "neimanmarcus.com", "sephora.com", "ulta.com", "lululemon.com", "aritzia.com",
})
SOCIAL_DOMAINS = frozenset({
    "facebook.com", "instagram.com", "pinterest.com", "pin.it", "twitter.com", "x.com", "tiktok.com",
    "youtube.com", "youtu.be", "linkedin.com", "threads.net", "bsky.app", "whatsapp.com", "snapchat.com",
})


@dataclass(frozen=True)
class ImageCandidate:
    """An image of the article, before download.

    Attributes:
        url (str): Absolute URL of the chosen size variant.
        alt (Optional[str]): Alt text.
        caption (Optional[str]): Caption from ``figcaption`` or ``.wp-caption-text``.
        shop_linked (bool): Whether the image links to a shop or affiliate network.
        upload_month (Optional[date]): First day of the upload month from a
            WordPress upload path.
    """
    url: str
    alt: Optional[str] = None
    caption: Optional[str] = None
    shop_linked: bool = False
    upload_month: Optional[date] = None


@dataclass(frozen=True)
class PostContent:
    """The parts of a post that the pipeline uses.

    Attributes:
        text (str): Article text with collapsed whitespace, at most MAX_TEXT_CHARS.
        images (tuple[ImageCandidate, ...]): Article images in page order; images
            that link to shops come last.
        link_texts (tuple[str, ...]): Texts of the external links, without social media.
        shopping_link_count (int): Number of links to shops or affiliate networks.
        canonical_url (Optional[str]): Canonical URL declared by the page.
        matched_selector (Optional[str]): Selector of the article element, or
            None if the whole document was used.
    """
    text: str
    images: tuple[ImageCandidate, ...]
    link_texts: tuple[str, ...]
    shopping_link_count: int
    canonical_url: Optional[str]
    matched_selector: Optional[str]


def _collapse(text: Optional[str]) -> Optional[str]:
    """Collapses whitespace; returns None for empty text."""
    collapsed = " ".join((text or "").split())
    return collapsed or None


def _host_matches(host: str, domains: frozenset[str]) -> bool:
    """Returns True if a host is one of the domains or a subdomain of one."""
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _host(url: str) -> str:
    """Returns the lowercase host of a URL without www., or an empty string."""
    try:
        return domain_id(url)
    except ValueError:
        return ""


def _find_article(soup: BeautifulSoup) -> tuple[Tag, Optional[str]]:
    """Selects the article element.

    For each selector, the match with the most text is taken, because themes
    render related posts as further ``article`` elements. A match counts only
    if it contains an image or at least MIN_ARTICLE_TEXT_CHARS characters.

    Args:
        soup: The parsed document.

    Returns:
        tuple: The element and its selector, or the body (or document) and None.
    """
    for selector in ARTICLE_SELECTORS:
        matches = soup.select(selector)
        if not matches:
            continue
        best = max(matches, key=lambda element: len(element.get_text(" ", strip=True)))
        if best.find("img") is not None or len(best.get_text(" ", strip=True)) >= MIN_ARTICLE_TEXT_CHARS:
            return best, selector
    return (soup.body or soup), None


def _srcset_entries(value: str) -> list[tuple[int, str]]:
    """Parses a ``srcset`` attribute into ``(width, url)`` pairs; density descriptors are ignored."""
    entries = []
    for match in re.finditer(r"(\S+)\s+(\d+)w", value):
        url = match.group(1).strip(",")
        if url:
            entries.append((int(match.group(2)), url))
    return entries


def _choose_image_url(img: Tag) -> Optional[str]:
    """Chooses the image URL: the srcset variant closest to TARGET_IMAGE_WIDTH,
    else a linked full-size image, else the (lazy-loading) source attribute.

    Args:
        img: The ``img`` element.

    Returns:
        Optional[str]: The URL as written in the page, or None.
    """
    entries: list[tuple[int, str]] = []
    for attribute in SRCSET_ATTRIBUTES:
        entries.extend(_srcset_entries(img.get(attribute) or ""))
    picture = img.find_parent("picture")
    if picture is not None:
        for source in picture.find_all("source"):
            entries.extend(_srcset_entries(source.get("srcset") or source.get("data-srcset") or ""))
    if entries:
        return min(entries, key=lambda entry: abs(entry[0] - TARGET_IMAGE_WIDTH))[1]
    link = img.find_parent("a")
    href = (link.get("href") or "") if link is not None else ""
    if href and (IMAGE_LINK_PATTERN.search(href) or _host_matches(_host(href), frozenset(IMAGE_LINK_HOSTS))):
        return href
    for attribute in SRC_ATTRIBUTES:
        value = (img.get(attribute) or "").strip()
        if value and not value.startswith("data:"):
            return value
    return None


def _image_key(url: str) -> str:
    """Returns a key that is equal for size variants of the same image.

    Removes WordPress size suffixes (``-1024x1536``), Blogger size segments
    (``/s1600/``, ``=s320``) and the query string.
    """
    parts = urlsplit(url)
    path = re.sub(r"-\d+x\d+(?=\.\w+$)", "", parts.path)
    path = re.sub(r"/(s|w)\d+(-[a-z0-9-]+)?/", "/", path)
    path = re.sub(r"=[swh]\d+[^/]*$", "", path)
    return f"{parts.netloc.lower()}{path}"


def _caption(img: Tag) -> Optional[str]:
    """Returns the caption of an image from ``figcaption`` or ``.wp-caption-text``."""
    figure = img.find_parent("figure")
    if figure is not None and figure.find("figcaption") is not None:
        return _collapse(figure.find("figcaption").get_text(" ", strip=True))
    wrapper = img.find_parent(class_="wp-caption")
    if wrapper is not None and wrapper.find(class_="wp-caption-text") is not None:
        return _collapse(wrapper.find(class_="wp-caption-text").get_text(" ", strip=True))
    return None


def _upload_month(url: str) -> Optional[date]:
    """Returns the upload month of a WordPress image URL."""
    match = WORDPRESS_UPLOAD_PATTERN.search(url)
    if match is None:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    return date(year, month, 1) if 1 <= month <= 12 else None


def _extract_links(article: Tag, page_url: str) -> tuple[tuple[str, ...], int]:
    """Collects the external links of the article.

    Args:
        article: The article element.
        page_url: URL of the post.

    Returns:
        tuple: Link texts without social media and duplicates, and the number of shopping links.
    """
    own = _host(page_url)
    texts: dict[str, None] = {}
    shopping = 0
    for link in article.find_all("a", href=True):
        href = urljoin(page_url, link["href"])
        if not href.startswith(("http://", "https://")):
            continue
        host = _host(href)
        if not host or host == own or host.endswith("." + own) or own.endswith("." + host):
            continue
        if _host_matches(host, SOCIAL_DOMAINS):
            continue
        if _host_matches(host, SHOP_DOMAINS):
            shopping += 1
        text = _collapse(link.get_text(" ", strip=True))
        if text:
            texts.setdefault(text, None)
    return tuple(texts), shopping


def _extract_images(article: Tag, page_url: str) -> tuple[ImageCandidate, ...]:
    """Collects the images of the article without size duplicates.

    Args:
        article: The article element, with noise and shop widgets removed.
        page_url: URL of the post.

    Returns:
        tuple: Images in page order, with shop-linked images moved to the end.
    """
    seen: set[str] = set()
    plain: list[ImageCandidate] = []
    linked: list[ImageCandidate] = []
    for img in article.find_all("img"):
        chosen = _choose_image_url(img)
        if not chosen:
            continue
        url = urljoin(page_url, chosen)
        if not url.startswith(("http://", "https://")):
            continue
        key = _image_key(url)
        if key in seen:
            continue
        seen.add(key)
        link = img.find_parent("a")
        shop_linked = link is not None and _host_matches(_host(urljoin(page_url, link.get("href") or "")), SHOP_DOMAINS)
        candidate = ImageCandidate(
            url=url,
            alt=_collapse(img.get("alt")),
            caption=_caption(img),
            shop_linked=shop_linked,
            upload_month=_upload_month(url),
        )
        (linked if shop_linked else plain).append(candidate)
    return tuple(plain + linked)


def extract_post_content(html: str, page_url: str, fragment: bool = False) -> PostContent:
    """Extracts text, images, links and the canonical URL of a post.

    Args:
        html: The page HTML, or the content HTML of a feed entry.
        page_url: URL of the post, used to resolve relative URLs.
        fragment: True for feed content, which is the article itself.

    Returns:
        PostContent: The extracted content.
    """
    soup = BeautifulSoup(html, "html.parser")
    canonical_link = soup.select_one('link[rel~="canonical"][href]')
    canonical_url = urljoin(page_url, canonical_link["href"]) if canonical_link is not None else None
    if fragment:
        article, selector = soup, None
    else:
        article, selector = _find_article(soup)
    # Matches inside an already removed element are skipped: decompose() also clears their attributes
    for element in article.select(", ".join(NOISE_SELECTORS)):
        if not element.decomposed:
            element.decompose()
    link_texts, shopping_link_count = _extract_links(article, page_url)
    for element in article.find_all(True):
        if element.decomposed:
            continue
        marker = " ".join([*element.get("class", []), element.get("id") or ""])
        if marker.strip() and SHOP_WIDGET_PATTERN.search(marker):
            element.decompose()
    text = " ".join(article.get_text(" ", strip=True).split())
    return PostContent(
        text=text[:MAX_TEXT_CHARS],
        images=_extract_images(article, page_url),
        link_texts=link_texts,
        shopping_link_count=shopping_link_count,
        canonical_url=canonical_url,
        matched_selector=selector,
    )


def is_republished(images: Sequence[ImageCandidate], published: Optional[date]) -> bool:
    """Returns True if most dated images were uploaded long before the post's publish date.

    Republished posts would place old outfits at a new date. Only WordPress
    upload paths carry a date; posts without dated images are never flagged.

    Args:
        images: The image candidates of the post.
        published: Publish date from the feed.

    Returns:
        bool: True if more than half of the dated images are more than
        REPUBLISHED_AFTER_MONTHS months older than the publish date.
    """
    months = [image.upload_month for image in images if image.upload_month is not None]
    if published is None or not months:
        return False
    published_index = published.year * 12 + published.month
    old = sum(1 for month in months if published_index - (month.year * 12 + month.month) > REPUBLISHED_AFTER_MONTHS)
    return old * 2 > len(months)
