"""Tests for the page fetching in src/crawl.py, with a fake crawler instead of a browser."""
import asyncio
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from crawl4ai import CacheMode

from crawl import PageFetchError, fetch_post


class FakeCrawler:
    """Returns a prepared crawl result or raises a prepared error."""

    def __init__(self, result: Any = None, error: Optional[Exception] = None) -> None:
        self.result = result
        self.error = error
        self.configs: list[Any] = []

    async def arun(self, url: str, config: Any) -> Any:
        self.configs.append(config)
        if self.error is not None:
            raise self.error
        return self.result


def crawl_result(status: Optional[int], success: bool = True, html: str = "<html><body>Post</body></html>",
                 redirected_url: Optional[str] = None) -> SimpleNamespace:
    """Builds an object with the attributes of a crawl4ai result."""
    return SimpleNamespace(success=success, status_code=status, html=html, redirected_url=redirected_url,
                           error_message=None if success else "net::ERR_NAME_NOT_RESOLVED")


def test_successful_fetch_returns_the_final_url() -> None:
    crawler = FakeCrawler(crawl_result(200, redirected_url="https://example.com/post-final/"))
    page = asyncio.run(fetch_post(crawler, "https://example.com/post/"))
    assert (page.url, page.status_code) == ("https://example.com/post-final/", 200)
    config = crawler.configs[0]
    assert config.cache_mode == CacheMode.BYPASS and config.magic is False


@pytest.mark.parametrize(
    ("result", "status", "transient"),
    [
        (crawl_result(404), 404, False),
        (crawl_result(503), 503, True),
        (crawl_result(429), 429, True),
        (crawl_result(None, success=False, html=""), None, False),
        (crawl_result(200, html=""), 200, False),
    ],
)
def test_error_pages_raise(result: SimpleNamespace, status: Optional[int], transient: bool) -> None:
    with pytest.raises(PageFetchError) as error:
        asyncio.run(fetch_post(FakeCrawler(result), "https://example.com/post/"))
    assert (error.value.status_code, error.value.transient) == (status, transient)


def test_crawler_errors_raise() -> None:
    with pytest.raises(PageFetchError):
        asyncio.run(fetch_post(FakeCrawler(error=RuntimeError("browser closed")), "https://example.com/post/"))
