"""Tests for feed parsing, feed discovery and post selection in src/feeds.py."""
from datetime import date, timedelta
from pathlib import Path

import pytest

from feeds import (
    FeedError,
    FeedPost,
    SourceFeed,
    count_recent_posts,
    fetch_feed,
    find_feed,
    is_non_fashion,
    parse_feed,
    select_posts,
)
from settings import Settings
from state import PipelineState
from urls import normalize_url

FEEDS_DIR = Path(__file__).parent / "fixtures" / "feeds"
TODAY = date(2026, 10, 10)
WORDPRESS_FEED = "https://www.example-style.com/feed/"


def read_fixture(name: str) -> bytes:
    """Returns a fixture file as bytes."""
    return (FEEDS_DIR / name).read_bytes()


def test_parse_wordpress_rss() -> None:
    feed = parse_feed(read_fixture("wordpress_rss.xml"), WORDPRESS_FEED)
    assert feed is not None
    assert feed.language == "en-GB"
    assert feed.newest_post == date(2026, 10, 8)
    first = feed.posts[0]
    assert first.url == "https://www.example-style.com/autumn-outfit/?utm_source=rss&utm_medium=rss"
    assert first.title == "Autumn outfit: camel coat and wide-leg jeans"
    assert first.published == date(2026, 10, 8)
    assert first.tags == ("Outfits", "Autumn")
    # Sanitizing is off, so lazy-loading and srcset attributes survive
    assert "srcset=" in first.content_html and "data-lazy-src=" in first.content_html
    assert feed.posts[2].content_html is None


def test_parse_blogger_atom() -> None:
    feed = parse_feed(read_fixture("blogger_atom.xml"), "https://seaside.blogspot.com/feeds/posts/default")
    assert feed is not None
    assert feed.language is None
    assert [post.tags for post in feed.posts] == [("outfits",), ("books",)]
    assert feed.posts[0].url == "https://seaside.blogspot.com/2026/10/rainy-day-layers.html"
    assert "<img" in feed.posts[0].content_html


def test_html_is_not_a_feed() -> None:
    assert parse_feed(read_fixture("homepage.html"), "https://www.example-style.com/") is None


def test_find_feed_uses_the_linked_post_feed(web) -> None:
    web.add("https://www.example-style.com/", read_fixture("homepage.html"))
    web.add_feed(WORDPRESS_FEED, read_fixture("wordpress_rss.xml"))
    search = find_feed("https://www.example-style.com/", web)
    assert search.feed is not None and search.feed.url == WORDPRESS_FEED
    assert search.homepage_status == 200
    assert "https://www.example-style.com/comments/feed/" not in web.requested


def test_find_feed_falls_back_to_common_paths(web) -> None:
    web.add("https://seaside.blogspot.com/", b"<html><head></head><body>No feed link</body></html>")
    web.add("https://seaside.blogspot.com/feeds/posts/default", read_fixture("blogger_atom.xml"),
            content_type="application/atom+xml")
    assert find_feed("https://seaside.blogspot.com/", web).feed is not None
    assert web.requested[:3] == [
        "https://seaside.blogspot.com/",
        "https://seaside.blogspot.com/feed/",
        "https://seaside.blogspot.com/feeds/posts/default",
    ]


def test_find_feed_follows_the_homepage_redirect(web) -> None:
    web.add("https://example-style.com/", read_fixture("homepage.html"), final_url="https://www.example-style.com/")
    web.add_feed(WORDPRESS_FEED, read_fixture("wordpress_rss.xml"))
    assert find_feed("https://example-style.com/", web).feed is not None


def test_empty_page_feed_is_skipped_for_the_blog_feed(web, rss) -> None:
    # Squarespace links the feed of the static homepage, which has no entries
    web.add("https://square.example/",
            b'<html><head><link rel="alternate" type="application/rss+xml" href="/home?format=rss"></head></html>')
    web.add_feed("https://square.example/home?format=rss", rss([]))
    web.add_feed("https://square.example/blog?format=rss", rss([("Fall look", "https://square.example/blog/fall", TODAY)]))
    search = find_feed("https://square.example/", web)
    assert search.feed.url == "https://square.example/blog?format=rss"


def test_empty_feed_is_the_last_resort(web, rss) -> None:
    web.add_feed("https://quiet.example/feed/", rss([]))
    search = find_feed("https://quiet.example/", web)
    assert search.feed is not None and search.feed.posts == ()


def test_blocked_homepage_is_not_probed_further(web) -> None:
    web.add("https://blocked.example/", b"Just a moment...", status=403)
    search = find_feed("https://blocked.example/", web)
    assert (search.feed, search.homepage_status) == (None, 403)
    assert web.requested == ["https://blocked.example/"]


def test_find_feed_without_any_feed(web) -> None:
    web.unreachable.add("https://gone.example/")
    search = find_feed("https://gone.example/", web)
    assert (search.feed, search.homepage_status) == (None, None)


def test_fetch_feed_errors(web) -> None:
    web.add("https://a.example/feed/", b"", status=500)
    web.add("https://b.example/feed/", read_fixture("homepage.html"))
    web.unreachable.add("https://c.example/feed/")
    for url in ("https://a.example/feed/", "https://b.example/feed/", "https://c.example/feed/"):
        with pytest.raises(FeedError):
            fetch_feed(url, web)


@pytest.mark.parametrize(
    ("title", "tags", "expected"),
    [
        ("Weekend Reading 10.10.26", (), True),
        ("Fall lookbook", ("Outfits",), False),
        ("What I wore this week", ("Books",), True),
        ("Holiday gift guide", (), True),
        ("Decorating with plaid", ("Home Decor",), True),
        ("Facebook live try-on", (), False),
    ],
)
def test_is_non_fashion(title: str, tags: tuple, expected: bool) -> None:
    assert is_non_fashion(title, tags) is expected


def test_count_recent_posts() -> None:
    feed = parse_feed(read_fixture("wordpress_rss.xml"), WORDPRESS_FEED)
    assert count_recent_posts(feed, TODAY, 60) == 3
    assert count_recent_posts(feed, TODAY, 5) == 1


def settings(**overrides: int) -> Settings:
    """Returns settings for the selection tests."""
    values = {"max_posts_per_run": 40, "max_posts_per_source": 3, "max_probation_posts_per_run": 10,
              "feed_lookback_days": 30}
    values.update(overrides)
    return Settings(**values)


def test_selection_filters_by_date_topic_and_state(tmp_path: Path) -> None:
    feed = parse_feed(read_fixture("wordpress_rss.xml"), WORDPRESS_FEED)
    state = PipelineState(tmp_path, {}, {})
    selected = select_posts([SourceFeed("example-style.com", False, feed.posts)], state, settings(), TODAY)
    # Oldest first; the reading list and the post from August are left out
    assert [post.title for post in selected] == ["Striped shirt three ways", "Autumn outfit: camel coat and wide-leg jeans"]
    assert selected[1].key == "https://example-style.com/autumn-outfit"
    assert selected[1].content_html is not None

    state.register(selected[0].key, source_id="example-style.com", url=selected[0].url, title=selected[0].title,
                   published=selected[0].published, tags=selected[0].tags, today=TODAY)
    state.complete(selected[0].key, "done", today=TODAY, outfits=1)
    again = select_posts([SourceFeed("example-style.com", False, feed.posts)], state, settings(), TODAY)
    assert [post.title for post in again] == ["Autumn outfit: camel coat and wide-leg jeans"]


def test_pending_posts_are_retried_after_leaving_the_feed(tmp_path: Path) -> None:
    state = PipelineState(tmp_path, {}, {})
    url = "https://example-style.com/old-but-pending/"
    state.register(normalize_url(url), source_id="example-style.com", url=url, title="Pending",
                   published=TODAY - timedelta(days=3), tags=("Outfits",), today=TODAY - timedelta(days=2))
    selected = select_posts([SourceFeed("example-style.com", False, ())], state, settings(), TODAY)
    assert [(post.url, post.retry) for post in selected] == [(url, True)]


def posts_for(source: str, days_ago: list[int]) -> tuple[FeedPost, ...]:
    """Builds feed posts of a source with publish dates ``days_ago`` before TODAY."""
    return tuple(FeedPost(url=f"https://{source}/post-{d}", title=f"{source} {d}", published=TODAY - timedelta(days=d))
                 for d in days_ago)


def test_sources_take_turns_and_caps_apply(tmp_path: Path) -> None:
    state = PipelineState(tmp_path, {}, {})
    feeds = [
        SourceFeed("a.example", False, posts_for("a.example", [1, 2, 3])),
        SourceFeed("b.example", False, posts_for("b.example", [5, 6])),
        SourceFeed("c.example", False, posts_for("c.example", [9])),
    ]
    selected = select_posts(feeds, state, settings(max_posts_per_source=2, max_posts_per_run=4), TODAY)
    # Round 1: the oldest post of each source, oldest first; round 2 until the run is full
    assert [post.title for post in selected] == ["c.example 9", "b.example 6", "a.example 3", "b.example 5"]


def test_probation_sources_have_a_quota(tmp_path: Path) -> None:
    state = PipelineState(tmp_path, {}, {})
    feeds = [
        SourceFeed("new1.example", True, posts_for("new1.example", [1, 2])),
        SourceFeed("new2.example", True, posts_for("new2.example", [3])),
        SourceFeed("old.example", False, posts_for("old.example", [4])),
    ]
    selected = select_posts(feeds, state, settings(max_probation_posts_per_run=1), TODAY)
    assert [post.source_id for post in selected] == ["old.example", "new2.example"]
