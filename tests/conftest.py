"""Shared test helpers: a fake web for the fetcher functions, an RSS builder and an image builder."""
import io
import random
from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone
from email.utils import format_datetime
from typing import Optional
from xml.sax.saxutils import escape

import pytest
import requests
from PIL import Image

from feeds import HttpResponse


class FakeWeb:
    """Fetcher that serves prepared responses and records the requested URLs."""

    def __init__(self) -> None:
        self.pages: dict[str, HttpResponse] = {}
        self.unreachable: set[str] = set()
        self.requested: list[str] = []

    def add(self, url: str, content: bytes, status: int = 200, content_type: str = "text/html",
            final_url: Optional[str] = None) -> None:
        """Serves ``content`` for ``url``."""
        self.pages[url] = HttpResponse(url=final_url or url, status=status, content=content, content_type=content_type)

    def add_feed(self, url: str, content: bytes) -> None:
        """Serves a feed for ``url``."""
        self.add(url, content, content_type="application/rss+xml; charset=UTF-8")

    def __call__(self, url: str) -> HttpResponse:
        self.requested.append(url)
        if url in self.unreachable:
            raise requests.ConnectionError(f"cannot reach {url}")
        return self.pages.get(url, HttpResponse(url=url, status=404, content=b"Not found", content_type="text/html"))


def make_rss(items: Sequence[tuple[str, str, date]], language: Optional[str] = "en-US") -> bytes:
    """Builds an RSS 2.0 feed.

    Args:
        items: ``(title, link, published)`` per entry.
        language: Channel language tag, or None to omit it.

    Returns:
        bytes: The feed XML.
    """
    entries = "".join(
        f"<item><title>{escape(title)}</title><link>{escape(link)}</link>"
        f"<pubDate>{format_datetime(datetime(d.year, d.month, d.day, 8, tzinfo=timezone.utc))}</pubDate></item>"
        for title, link, d in items
    )
    language_tag = f"<language>{language}</language>" if language else ""
    return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Blog</title>'
            f"<link>https://example.com/</link><description>d</description>{language_tag}{entries}</channel></rss>"
            ).encode("utf-8")


def make_jpeg(seed: int, size: tuple[int, int] = (600, 900)) -> bytes:
    """Builds a JPEG of a smooth random picture; different seeds give clearly different dHashes.

    Args:
        seed: Seed of the picture.
        size: Width and height in pixels.

    Returns:
        bytes: The JPEG data.
    """
    rng = random.Random(seed)
    grid = Image.new("L", (12, 18))
    grid.putdata([rng.randrange(256) for _ in range(12 * 18)])
    buffer = io.BytesIO()
    grid.resize(size, Image.Resampling.BICUBIC).convert("RGB").save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def web() -> FakeWeb:
    """A fresh fake web per test."""
    return FakeWeb()


@pytest.fixture
def jpeg() -> Callable[..., bytes]:
    """The JPEG builder."""
    return make_jpeg


@pytest.fixture
def rss() -> Callable[..., bytes]:
    """The RSS builder."""
    return make_rss
