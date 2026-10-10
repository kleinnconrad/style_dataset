"""
Fetches the rendered HTML of post pages with crawl4ai.

Only the HTML is taken from crawl4ai: its image and link lists cover the whole
page, so the article is parsed separately (see content.py). One browser
instance serves all posts of a run.
"""
import logging
from dataclasses import dataclass
from typing import Any, Optional

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

logger = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 60_000

# Explicit settings; crawl4ai 0.9.4 ignores keyword arguments to arun() that are not part of the config.
# magic stays off: the crawler does not try to get around bot protection.
RUN_CONFIG = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=PAGE_TIMEOUT_MS, verbose=False)


class PageFetchError(Exception):
    """A post page could not be fetched.

    Attributes:
        status_code (Optional[int]): HTTP status, or None if no response was received.
    """

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def transient(self) -> bool:
        """True for rate limiting and server errors, which say nothing about the post itself."""
        return self.status_code is not None and (self.status_code == 429 or self.status_code >= 500)


@dataclass(frozen=True)
class FetchedPage:
    """The rendered HTML of a post page.

    Attributes:
        url (str): Final URL after redirects.
        status_code (int): HTTP status.
        html (str): The rendered HTML.
    """
    url: str
    status_code: int
    html: str


def create_crawler() -> AsyncWebCrawler:
    """Creates the headless browser crawler; use it as an async context manager."""
    return AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False))


async def fetch_post(crawler: Any, url: str) -> FetchedPage:
    """Fetches the rendered HTML of a post page.

    crawl4ai reports success for any page with HTML, including error pages, so
    the HTTP status is checked here.

    Args:
        crawler: A started ``AsyncWebCrawler`` or a compatible test double.
        url: URL of the post.

    Returns:
        FetchedPage: The page.

    Raises:
        PageFetchError: If crawling fails or the status is not 2xx.
    """
    try:
        result = await crawler.arun(url=url, config=RUN_CONFIG)
    except Exception as e:
        # crawl4ai passes on errors of Playwright and its own processing, which share no common base class
        raise PageFetchError(f"{url}: {type(e).__name__}: {e}") from e
    status = getattr(result, "status_code", None)
    html = getattr(result, "html", "") or ""
    if not getattr(result, "success", False) or status is None or not 200 <= status < 300 or not html:
        error = getattr(result, "error_message", None) or "no HTML"
        raise PageFetchError(f"{url}: HTTP {status} ({error})", status_code=status)
    return FetchedPage(url=getattr(result, "redirected_url", None) or url, status_code=status, html=html)
