"""Tests for the URL helpers in src/urls.py."""
import pytest

from urls import domain_id, normalize_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.Example.com/2026/10/post/", "https://example.com/2026/10/post"),
        ("http://example.com/2026/10/post", "https://example.com/2026/10/post"),
        ("https://example.com/post?utm_source=rss&utm_medium=feed", "https://example.com/post"),
        ("https://example.com/post?p=12&fbclid=abc", "https://example.com/post?p=12"),
        ("https://example.com/?b=2&a=1", "https://example.com/?a=1&b=2"),
        ("https://example.com/post#comments", "https://example.com/post"),
        ("https://example.com:443/post", "https://example.com/post"),
        ("https://example.com:8080/post", "https://example.com:8080/post"),
        ("https://example.com", "https://example.com/"),
        ("https://example.com/Case/Sensitive", "https://example.com/Case/Sensitive"),
    ],
)
def test_normalize_url(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


def test_variants_of_the_same_post_get_the_same_key() -> None:
    variants = [
        "https://www.example.com/fall-outfit/",
        "http://example.com/fall-outfit?utm_campaign=x",
        "https://EXAMPLE.com/fall-outfit#top",
    ]
    assert len({normalize_url(url) for url in variants}) == 1


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.styleandminimalism.com/", "styleandminimalism.com"),
        ("https://SeadBeady.blogspot.com/2026/01/post.html", "seadbeady.blogspot.com"),
    ],
)
def test_domain_id(url: str, expected: str) -> None:
    assert domain_id(url) == expected


def test_url_without_host_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_url("/relative/path")
