"""
Runs the pipeline for one day.

The run refreshes the feeds of the active and probation sources, selects the
posts, and for each post gets its content, selects its images, extracts the
outfits and saves records and state before the next post starts. A failing
post does not stop the run; quota and budget limits end it early and keep the
progress. In dry-run mode no model is called and nothing is saved except a
report.
"""
import asyncio
import json
import logging
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from content import PostContent, extract_post_content, is_republished
from crawl import FetchedPage, PageFetchError
from extraction import (
    FatalRequestError,
    ImageInput,
    PostContext,
    RunStopError,
    TransientExtractionError,
    UnusableResponseError,
    extraction_hash,
)
from feeds import FeedError, Fetcher, ParsedFeed, SelectedPost, SourceFeed, fetch_feed, http_get, select_posts
from images import ByteFetcher, ImageSelection, fetch_image_bytes, select_images
from records import PostMetadata, SourceMetadata, build_records, is_excluded
from schema import ImageRef
from settings import Settings
from sources import SourceEntry, SourceRegistry, record_feed_result, record_post_outcome, seed_from_legacy
from state import PipelineState, PostEntry, atomic_write_text
from storage import append_records
from urls import normalize_url

logger = logging.getLogger(__name__)

LEGACY_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
RUN_LOG_FILE = "run_log.jsonl"
DRY_RUN_REPORT_FILE = "dry_run_report.json"
FEED_WORKERS = 8


@dataclass
class Services:
    """External services of a run; tests replace them.

    Attributes:
        fetch_page (Callable): Returns the rendered HTML of a post page (see crawl.fetch_post).
        extractor (Optional[Any]): A ``GeminiExtractor``; None in dry-run mode.
        fetch (Fetcher): Fetches feeds and homepages.
        fetch_image (ByteFetcher): Downloads images.
    """
    fetch_page: Callable[[str], Awaitable[FetchedPage]]
    extractor: Optional[Any] = None
    fetch: Fetcher = http_get
    fetch_image: ByteFetcher = fetch_image_bytes


@dataclass
class RunStats:
    """Counters of a run, written to the run log.

    Attributes:
        date (str): UTC date of the run.
        mode (str): ``normal`` or ``dry-run``.
        feeds_ok (int): Feeds fetched successfully.
        feeds_failed (int): Feeds that could not be fetched.
        posts_selected (int): Posts selected for the run.
        posts (Counter): Posts by outcome.
        images (Counter): Image candidates by filter result; ``selected`` for images sent to the model.
        model_rejections (Counter): Images the model did not assign to an outfit, by reason.
        outfits (int): Stored outfit records.
        minors_skipped (int): Outfits of minors that were not stored.
        usage (dict): Gemini calls and tokens.
        source_transitions (list): Status changes of sources.
        stopped_early (Optional[str]): Why extraction ended before all posts were processed.
        fatal (bool): Whether the API rejected requests in a way that needs attention.
    """
    date: str
    mode: str
    feeds_ok: int = 0
    feeds_failed: int = 0
    posts_selected: int = 0
    posts: Counter = field(default_factory=Counter)
    images: Counter = field(default_factory=Counter)
    model_rejections: Counter = field(default_factory=Counter)
    outfits: int = 0
    minors_skipped: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    source_transitions: list[dict[str, str]] = field(default_factory=list)
    stopped_early: Optional[str] = None
    fatal: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Returns the counters as plain JSON-compatible values."""
        values = asdict(self)
        for name in ("posts", "images", "model_rejections"):
            values[name] = dict(getattr(self, name))
        return values


class DailyRun:
    """One run of the pipeline."""

    def __init__(self, settings: Settings, today: date, services: Services, dry_run: bool = False) -> None:
        """Loads registry and state.

        Args:
            settings: The run settings.
            today: UTC date of the run.
            services: External services.
            dry_run: If True, no model is called and only a report is written.

        Raises:
            ValueError: If no extractor is given outside dry-run mode.
        """
        if not dry_run and services.extractor is None:
            raise ValueError("A run outside dry-run mode needs an extractor")
        self.settings = settings
        self.today = today
        self.services = services
        self.dry_run = dry_run
        self.registry = SourceRegistry.load(settings.state_dir)
        self.state = PipelineState.load(settings.state_dir)
        self.stats = RunStats(date=today.isoformat(), mode="dry-run" if dry_run else "normal")
        self.config_hash = extraction_hash(settings)
        self.report: dict[str, Any] = {"date": today.isoformat(), "sources": [], "posts": []}

    async def run(self, source_ids: Optional[Sequence[str]] = None, legacy_dir: Path = LEGACY_DATA_DIR) -> RunStats:
        """Executes the run.

        Args:
            source_ids: Restrict the run to these sources; None for all.
            legacy_dir: Folder with the legacy records, used to seed an empty registry.

        Returns:
            RunStats: The counters of the run.
        """
        if len(self.registry) == 0:
            seed_from_legacy(self.registry, legacy_dir, self.today, self.services.fetch)
            self._save()
        sources = [
            source for source in self.registry.with_status("active", "probation")
            if not source_ids or source.source_id in source_ids
        ]
        feeds = self._refresh_feeds(sources)
        posts = select_posts(feeds, self.state, self.settings, self.today)
        self.stats.posts_selected = len(posts)
        logger.info("%d posts selected from %d feeds.", len(posts), len(feeds))
        for number, post in enumerate(posts, start=1):
            logger.info("Post %d/%d: %s", number, len(posts), post.url)
            try:
                await self._process_post(post)
            except RunStopError as e:
                self.stats.stopped_early = f"{type(e).__name__}: {e}"
                self.stats.fatal = isinstance(e, FatalRequestError)
                logger.warning("Extraction stopped after %d posts: %s", number - 1, e)
                break
        self._finish()
        return self.stats

    def _refresh_feeds(self, sources: Sequence[SourceEntry]) -> list[SourceFeed]:
        """Fetches the feeds of the given sources and updates their feed statistics.

        Args:
            sources: Active and probation sources.

        Returns:
            list[SourceFeed]: The feeds that could be fetched.
        """
        def load(source: SourceEntry) -> tuple[SourceEntry, Optional[ParsedFeed]]:
            try:
                return source, fetch_feed(source.feed_url, self.services.fetch)
            except FeedError as e:
                logger.warning("Feed of %s failed: %s", source.source_id, e)
                return source, None

        with ThreadPoolExecutor(max_workers=FEED_WORKERS) as pool:
            results = list(pool.map(load, [source for source in sources if source.feed_url]))
        feeds = []
        for source, feed in results:
            self.registry.put(record_feed_result(source, feed, self.today))
            self.report["sources"].append({"source": source.source_id, "status": source.status,
                                           "feed": "ok" if feed else "failed"})
            if feed is None:
                self.stats.feeds_failed += 1
                continue
            self.stats.feeds_ok += 1
            feeds.append(SourceFeed(source.source_id, source.status == "probation", feed.posts))
        return feeds

    async def _content(self, post: SelectedPost, report: dict[str, Any]) -> Optional[PostContent]:
        """Returns the content of a post: from the feed if it includes images, else from the page.

        Args:
            post: The post.
            report: Report entry of the post, updated in place.

        Returns:
            Optional[PostContent]: The content, or None if the page could not be fetched.
        """
        if post.content_html:
            content = extract_post_content(post.content_html, post.url, fragment=True)
            if content.images:
                report["content_from"] = "feed"
                return content
        try:
            page = await self.services.fetch_page(post.url)
        except PageFetchError as e:
            report["error"] = str(e)
            self._fail(post, f"page: {e}", counts_as_attempt=not e.transient, outcome="page failed")
            return None
        content = extract_post_content(page.html, page.url)
        report["content_from"] = "page"
        report["selector"] = content.matched_selector
        source = self.registry.get(post.source_id)
        if source is not None and not self.dry_run:
            self.registry.put(source.model_copy(update={"matched_selector": content.matched_selector or "(body)"}))
        return content

    def _canonical_entry(self, post: SelectedPost, content: PostContent) -> Optional[PostEntry]:
        """Returns the state entry of the post's canonical URL, if it differs from the post URL.

        Args:
            post: The post.
            content: Its content with the canonical URL.

        Returns:
            Optional[PostEntry]: The entry, or None if there is none or the canonical URL is the post URL.
        """
        if not content.canonical_url:
            return None
        try:
            key = normalize_url(content.canonical_url)
        except ValueError:
            return None
        return self.state.get(key) if key != post.key else None

    async def _process_post(self, post: SelectedPost) -> None:
        """Processes one post and saves the result.

        Args:
            post: The selected post.

        Raises:
            RunStopError: If quota, call budget or a client error end the run.
        """
        report: dict[str, Any] = {"source": post.source_id, "post": post.url, "retry": post.retry}
        self.report["posts"].append(report)
        if not self.dry_run:
            self.state.register(post.key, source_id=post.source_id, url=post.url, title=post.title,
                                published=post.published, tags=post.tags, today=self.today)
        content = await self._content(post, report)
        if content is None:
            return
        known = self._canonical_entry(post, content)
        if known is not None and known.status != "pending":
            report["outcome"] = "duplicate"
            self._complete(post, "done", content, outcome="duplicate")
            return
        report["candidates"] = len(content.images)
        if is_republished(content.images, post.published):
            report["outcome"] = "republished"
            self._complete(post, "republished", content, outcome="republished")
            return
        selection = await asyncio.to_thread(
            select_images, content.images, self.state.find_similar_image, self.settings.max_images_per_post,
            post.url, self.services.fetch_image,
        )
        self.stats.images.update(selection.rejected)
        self.stats.images["selected"] += len(selection.images)
        report["selected"] = len(selection.images)
        report["rejected"] = dict(selection.rejected)
        if not selection.images:
            report["outcome"] = "no images"
            self._complete(post, "no_images", content, outcome="no images", produced_outfit=False)
            return
        if self.dry_run:
            report["outcome"] = "would call the model"
            self.stats.posts["would extract"] += 1
            return
        await self._extract(post, content, selection, report)

    async def _extract(self, post: SelectedPost, content: PostContent, selection: ImageSelection,
                       report: dict[str, Any]) -> None:
        """Calls the model, stores the records and completes the post.

        Args:
            post: The post.
            content: Its content.
            selection: Its selected images.
            report: Report entry of the post, updated in place.
        """
        context = PostContext(
            title=post.title,
            published_date=post.published.isoformat() if post.published else None,
            tags=post.tags,
            text=content.text,
            link_texts=content.link_texts,
        )
        inputs = [ImageInput(jpeg=image.jpeg, alt=image.candidate.alt, caption=image.candidate.caption)
                  for image in selection.images]
        try:
            result = await asyncio.to_thread(self.services.extractor.extract, context, inputs)
        except TransientExtractionError as e:
            self._fail(post, f"model: {e}", counts_as_attempt=False, outcome="model unavailable")
            return
        except UnusableResponseError as e:
            self._fail(post, f"model: {e}", counts_as_attempt=True, outcome="model response unusable")
            return
        refs = [ImageRef(hash=image.hash, width=image.width, height=image.height) for image in selection.images]
        source = self.registry.get(post.source_id)
        records = build_records(
            result.extraction,
            refs,
            PostMetadata(url=post.url, canonical_url=content.canonical_url, title=post.title,
                         published_date=context.published_date, tags=post.tags,
                         shopping_link_count=content.shopping_link_count),
            SourceMetadata(source_id=post.source_id, country=source.country if source else None,
                           country_basis=source.country_basis if source else "unknown",
                           language=source.language if source else None),
            model=self.settings.gemini_model,
            extraction_hash=self.config_hash,
            date_scraped=self.today.isoformat(),
        )
        self.stats.minors_skipped += sum(1 for outfit in result.extraction.outfits if is_excluded(outfit))
        self.stats.model_rejections.update(item.reason for item in result.extraction.rejected_images)
        self.stats.model_rejections["unassigned"] += len(result.unassigned_indexes)
        self.stats.model_rejections["unusable"] += len(result.unusable_indexes)
        self.stats.outfits += len(records)
        report.update(outcome="done", outfits=len(records))
        # Records first, then state: a crash in between leads to a reprocessing that replaces the records
        append_records(self.settings.output_dir, self.today, records, post_key=post.key)
        unusable = set(result.unusable_indexes)
        hashes = [image.hash for number, image in enumerate(selection.images, start=1) if number not in unusable]
        self._complete(post, "done", content, outcome="done", produced_outfit=bool(records),
                       outfits=len(records), image_hashes=hashes)

    def _complete(self, post: SelectedPost, status: str, content: PostContent, *, outcome: str,
                  produced_outfit: Optional[bool] = None, outfits: int = 0,
                  image_hashes: Sequence[str] = ()) -> None:
        """Marks a post as processed, updates its source and saves.

        Args:
            post: The post.
            status: Final post status.
            content: Content of the post.
            outcome: Name of the outcome in the run statistics.
            produced_outfit: Outcome for the source's yield; None if the post does not count.
            outfits: Number of stored records.
            image_hashes: Hashes of the analyzed images.
        """
        self.stats.posts[outcome] += 1
        if self.dry_run:
            return
        self.state.complete(post.key, status, today=self.today, outfits=outfits, image_hashes=image_hashes,
                            canonical_url=content.canonical_url)
        source = self.registry.get(post.source_id)
        if source is not None and produced_outfit is not None:
            self.registry.put(record_post_outcome(source, produced_outfit))
        self._save()

    def _fail(self, post: SelectedPost, error: str, *, counts_as_attempt: bool, outcome: str) -> None:
        """Records a failed attempt and saves.

        Args:
            post: The post.
            error: Description of the failure.
            counts_as_attempt: False for failures that say nothing about the post.
            outcome: Name of the outcome in the run statistics.
        """
        logger.warning("Post %s failed: %s", post.url, error)
        self.stats.posts[outcome] += 1
        if self.dry_run:
            return
        self.state.fail(post.key, error, counts_as_attempt=counts_as_attempt, today=self.today)
        self._save()

    def _save(self) -> None:
        """Writes state and registry, except in dry-run mode."""
        if self.dry_run:
            return
        self.state.save()
        self.registry.save()

    def _finish(self) -> None:
        """Applies the source rules, saves, and writes the run log or the dry-run report."""
        if self.services.extractor is not None:
            usage = self.services.extractor.usage
            self.stats.usage = {"calls": usage.calls, "prompt_tokens": usage.prompt_tokens,
                                "output_tokens": usage.output_tokens, "thinking_tokens": usage.thinking_tokens,
                                "total_tokens": usage.total_tokens}
        if self.dry_run:
            self.report["stats"] = self.stats.to_dict()
            path = self.settings.output_dir / DRY_RUN_REPORT_FILE
            atomic_write_text(path, json.dumps(self.report, indent=2, ensure_ascii=False) + "\n")
            logger.info("Dry-run report: %s", path)
            return
        expired = self.state.expire_pending(self.today, self.settings.feed_lookback_days)
        if expired:
            logger.info("%d pending posts were given up.", expired)
        self.stats.source_transitions = [
            {"source": source_id, "from": old, "to": new} for source_id, old, new in self.registry.apply_rules(self.today)
        ]
        self._save()
        log_path = self.settings.state_dir / RUN_LOG_FILE
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **self.stats.to_dict()}
        with open(log_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
