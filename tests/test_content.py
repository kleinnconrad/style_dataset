"""Tests for the article extraction in src/content.py."""
from datetime import date
from pathlib import Path

import pytest

from content import ImageCandidate, extract_post_content, is_republished

PAGES_DIR = Path(__file__).parent / "fixtures" / "pages"
UPLOADS = "https://www.example-style.com/wp-content/uploads/2026/10/"


def load_page(name: str) -> str:
    """Returns a page fixture as text."""
    return (PAGES_DIR / name).read_text(encoding="utf-8")


def test_wordpress_article_images() -> None:
    content = extract_post_content(load_page("wordpress_post.html"), "https://www.example-style.com/autumn-outfit/")
    assert content.matched_selector == ".entry-content"
    # Logo, sidebar, related posts, share buttons and the shop widget are left out; size
    # variants and the noscript copy are merged; the shop-linked photo comes last
    assert [image.url for image in content.images] == [
        UPLOADS + "look-1-1366x2048.jpg",
        UPLOADS + "look-2-1024x1536.jpg",
        UPLOADS + "look-3.jpg",
    ]
    first, second, third = content.images
    assert (first.alt, first.caption, first.upload_month) == ("Camel coat outfit", "Coat: Everlane, jeans: Nordstrom",
                                                              date(2026, 10, 1))
    assert (second.alt, second.caption) == (None, "Side view")
    assert third.shop_linked and not first.shop_linked


def test_wordpress_article_text_and_links() -> None:
    content = extract_post_content(load_page("wordpress_post.html"), "https://www.example-style.com/autumn-outfit/")
    assert content.canonical_url == "https://www.example-style.com/autumn-outfit/"
    assert content.link_texts == ("Everlane coat", "Nordstrom jeans", "this Vogue article")
    # shopltk, nordstrom, rstyle and the widget link to shopstyle
    assert content.shopping_link_count == 4
    assert "Coat: Everlane, jeans: Nordstrom" in content.text
    for excluded in ("Related post text", "Sidebar text", "Teaser text"):
        assert excluded not in content.text


def test_affiliate_redirect_domains_count_as_shopping_links() -> None:
    html = ("<div class='entry-content'><p>" + "Text. " * 50 + "</p>"
            "<a href='https://boden-uk.sjv.io/c/1/2/3'>Boden cardigan</a>"
            "<a href='https://amzlink.to/az0abc'>Amazon sweater</a>"
            "<a href='https://shop.example.com/ebook'>My shop</a>"
            "<a href='https://www.pinterest.com/example'>Pinterest</a></div>")
    content = extract_post_content(html, "https://example.com/post/")
    assert content.shopping_link_count == 2
    assert content.link_texts == ("Boden cardigan", "Amazon sweater")


def test_blogger_uses_the_linked_full_size_images() -> None:
    content = extract_post_content(load_page("blogger_post.html"), "https://seaside.blogspot.com/2026/10/rainy-day-layers.html")
    assert content.matched_selector == ".post-body"
    assert [image.url for image in content.images] == [
        "https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEh1/s1600/rain-coat.jpg",
        "https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEh2/s1600/boots.jpg",
    ]
    assert "yellow raincoat" in content.text


def test_without_article_element_the_body_is_used() -> None:
    content = extract_post_content("<html><body><p>Short</p><img src='/look.jpg'></body></html>",
                                   "https://example.com/post/")
    assert content.matched_selector is None
    assert [image.url for image in content.images] == ["https://example.com/look.jpg"]


def test_selectors_are_tried_in_priority_order() -> None:
    html = ("<html><body><main><p>" + "Intro text. " * 30 + "</p>"
            "<div class='post-content'><img src='/look.jpg'></div></main></body></html>")
    assert extract_post_content(html, "https://example.com/post/").matched_selector == ".post-content"


def test_the_largest_article_element_wins() -> None:
    html = ("<html><body><article><p>Teaser</p></article>"
            "<article><p>" + "Outfit details. " * 20 + "</p><img src='/look.jpg'></article></body></html>")
    content = extract_post_content(html, "https://example.com/post/")
    assert content.matched_selector == "article"
    assert len(content.images) == 1


def test_feed_fragment_is_used_as_the_article() -> None:
    fragment = "<p>Coat by Boden.</p><img src='https://example.com/wp-content/uploads/2026/10/coat.jpg' alt='Coat'>"
    content = extract_post_content(fragment, "https://example.com/post/", fragment=True)
    assert content.matched_selector is None
    assert content.images[0].alt == "Coat"


def image_from(month: date) -> ImageCandidate:
    """Builds an image candidate with an upload month."""
    return ImageCandidate(url=f"https://example.com/wp-content/uploads/{month.year}/{month.month:02d}/a.jpg",
                          upload_month=month)


@pytest.mark.parametrize(
    ("months", "published", "expected"),
    [
        ([date(2015, 12, 1), date(2015, 12, 1)], date(2026, 10, 8), True),
        ([date(2026, 10, 1)], date(2026, 10, 8), False),
        ([date(2025, 10, 1)], date(2026, 10, 8), False),
        ([date(2025, 9, 1)], date(2026, 10, 8), True),
        ([date(2015, 12, 1), date(2026, 10, 1)], date(2026, 10, 8), False),
        ([date(2015, 12, 1)], None, False),
        ([], date(2026, 10, 8), False),
    ],
)
def test_is_republished(months: list, published: date, expected: bool) -> None:
    assert is_republished([image_from(month) for month in months], published) is expected
