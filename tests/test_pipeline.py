"""Integration tests for the daily run in src/pipeline.py, with fakes for all external services."""
import asyncio
import json
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import pytest

from crawl import FetchedPage, PageFetchError
from extraction import (
    ExtractionResult,
    FatalRequestError,
    ImageInput,
    PostContext,
    QuotaExhaustedError,
    UsageTotals,
)
from pipeline import DRY_RUN_REPORT_FILE, RUN_LOG_FILE, DailyRun, Services
from schema import PostExtraction
from settings import Settings
from sources import SourceEntry, SourceRegistry
from state import PipelineState
from storage import day_file, load_day

TODAY = date(2026, 10, 10)
BLOG = "blog.example"
FEED_URL = f"https://{BLOG}/feed/"
POSTS = [f"https://{BLOG}/look-one/", f"https://{BLOG}/look-two/"]
FIXTURE = Path(__file__).parent / "fixtures" / "post_extraction.json"


def image_urls(post_url: str, upload_path: str = "2026/10") -> list[str]:
    """Returns the two image URLs of a post page."""
    slug = post_url.rstrip("/").rsplit("/", 1)[-1]
    return [f"https://{BLOG}/wp-content/uploads/{upload_path}/{slug}-{n}.jpg" for n in (1, 2)]


def page_html(post_url: str, upload_path: str = "2026/10") -> str:
    """Builds a post page with two outfit photos."""
    figures = "".join(f"<figure><img src='{url}' alt='Look'><figcaption>Coat by Everlane</figcaption></figure>"
                      for url in image_urls(post_url, upload_path))
    return (f"<html><head><link rel='canonical' href='{post_url}'></head><body><div class='entry-content'>"
            f"<p>{'Outfit details. ' * 20}</p>{figures}</div></body></html>")


class FakePages:
    """Serves post pages; prepared errors are raised instead."""

    def __init__(self, upload_path: str = "2026/10", errors: Optional[dict[str, Exception]] = None) -> None:
        self.upload_path = upload_path
        self.errors = errors or {}
        self.requested: list[str] = []

    async def __call__(self, url: str) -> FetchedPage:
        self.requested.append(url)
        if url in self.errors:
            raise self.errors[url]
        return FetchedPage(url=url, status_code=200, html=page_html(url, self.upload_path))


class FakeExtractor:
    """Returns one outfit that covers all images; raises prepared errors on given calls."""

    def __init__(self, errors: Optional[dict[int, Exception]] = None, age_group: str = "Adult") -> None:
        self.errors = errors or {}
        self.age_group = age_group
        self.usage = UsageTotals()

    def extract(self, post: PostContext, images: Sequence[ImageInput]) -> ExtractionResult:
        self.usage.calls += 1
        if self.usage.calls in self.errors:
            raise self.errors[self.usage.calls]
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        outfit = payload["outfits"][0]
        outfit.update(image_indexes=list(range(1, len(images) + 1)), age_group=self.age_group)
        return ExtractionResult(extraction=PostExtraction(visual_analysis="Two photos of one outfit.",
                                                          outfits=[outfit], rejected_images=[]))


@pytest.fixture
def setup(tmp_path: Path, web: Any, rss: Any, jpeg: Any) -> dict[str, Any]:
    """Prepares settings, a registry with one active source, its feed and its images."""
    settings = Settings(output_dir=tmp_path / "out", max_posts_per_run=10)
    web.add_feed(FEED_URL, rss([("Look one", POSTS[0], TODAY - timedelta(days=2)),
                                ("Look two", POSTS[1], TODAY - timedelta(days=1))]))
    images = {url: jpeg(seed) for seed, url in enumerate(
        [*image_urls(POSTS[0]), *image_urls(POSTS[1]), *image_urls(POSTS[0], "2015/12"), *image_urls(POSTS[1], "2015/12")],
        start=1)}
    return {"settings": settings, "web": web, "images": images}


def add_source(settings: Settings, status: str = "active", outcomes: Optional[list[bool]] = None) -> None:
    """Stores a registry with the test blog."""
    registry = SourceRegistry.load(settings.state_dir)
    registry.put(SourceEntry(source_id=BLOG, homepage=f"https://{BLOG}/", feed_url=FEED_URL, status=status,
                             status_since=TODAY - timedelta(days=10), origin="legacy", added=TODAY - timedelta(days=10),
                             country="US", country_basis="domain", language="en", recent_outcomes=outcomes or []))
    registry.save()


def run(setup: dict[str, Any], extractor: Optional[FakeExtractor] = None, pages: Optional[FakePages] = None,
        dry_run: bool = False, legacy_dir: Optional[Path] = None) -> Any:
    """Executes one run with the fakes."""
    services = Services(
        fetch_page=pages or FakePages(),
        extractor=None if dry_run else (extractor or FakeExtractor()),
        fetch=setup["web"],
        fetch_image=lambda url, referer: setup["images"][url],
    )
    daily = DailyRun(setup["settings"], TODAY, services, dry_run=dry_run)
    kwargs = {"legacy_dir": legacy_dir} if legacy_dir else {}
    return asyncio.run(daily.run(**kwargs))


def stored(setup: dict[str, Any]) -> list[dict[str, Any]]:
    """Returns the records of the test day."""
    return load_day(day_file(setup["settings"].output_dir, TODAY))


def test_run_stores_records_state_and_log(setup: dict[str, Any]) -> None:
    add_source(setup["settings"])
    stats = run(setup)
    assert dict(stats.posts) == {"done": 2}
    records = stored(setup)
    assert [record["post_url"] for record in records] == POSTS
    assert {record["schema_version"] for record in records} == {"2.0"}
    assert all(len(record["images"]) == 2 for record in records)
    state = PipelineState.load(setup["settings"].state_dir)
    assert {entry.status for entry in state.posts.values()} == {"done"}
    source = SourceRegistry.load(setup["settings"].state_dir).get(BLOG)
    assert (source.posts_processed, source.posts_with_outfits) == (2, 2)
    log = (setup["settings"].state_dir / RUN_LOG_FILE).read_text(encoding="utf-8").splitlines()
    assert json.loads(log[-1])["outfits"] == 2


def test_second_run_adds_nothing(setup: dict[str, Any]) -> None:
    add_source(setup["settings"])
    run(setup)
    extractor = FakeExtractor()
    stats = run(setup, extractor=extractor)
    assert stats.posts_selected == 0 and extractor.usage.calls == 0
    assert len(stored(setup)) == 2


def test_interrupted_run_is_repaired_without_duplicates(setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    add_source(setup["settings"])

    def power_cut(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("power cut")

    # The run stops after the first post's records were written, before its state was saved
    with monkeypatch.context() as patch:
        patch.setattr(PipelineState, "complete", power_cut)
        with pytest.raises(RuntimeError):
            run(setup)
    assert len(stored(setup)) == 1
    run(setup)
    assert [record["post_url"] for record in stored(setup)] == POSTS


def test_failed_pages_do_not_stop_the_run(setup: dict[str, Any]) -> None:
    add_source(setup["settings"])
    pages = FakePages(errors={POSTS[0]: PageFetchError("gone", status_code=404)})
    stats = run(setup, pages=pages)
    assert dict(stats.posts) == {"page failed": 1, "done": 1}
    entry = PipelineState.load(setup["settings"].state_dir).get("https://blog.example/look-one")
    assert (entry.status, entry.attempts) == ("pending", 1)


def test_server_errors_do_not_count_as_attempts(setup: dict[str, Any]) -> None:
    add_source(setup["settings"])
    pages = FakePages(errors={POSTS[0]: PageFetchError("busy", status_code=503)})
    run(setup, pages=pages)
    assert PipelineState.load(setup["settings"].state_dir).get("https://blog.example/look-one").attempts == 0


def test_quota_stop_keeps_the_progress(setup: dict[str, Any]) -> None:
    add_source(setup["settings"])
    stats = run(setup, extractor=FakeExtractor(errors={2: QuotaExhaustedError("per-day quota")}))
    assert stats.stopped_early.startswith("QuotaExhaustedError") and not stats.fatal
    assert len(stored(setup)) == 1
    state = PipelineState.load(setup["settings"].state_dir)
    assert state.get("https://blog.example/look-two").status == "pending"
    assert (setup["settings"].state_dir / RUN_LOG_FILE).exists()


def test_rejected_requests_are_fatal(setup: dict[str, Any]) -> None:
    add_source(setup["settings"])
    stats = run(setup, extractor=FakeExtractor(errors={1: FatalRequestError("invalid key")}))
    assert stats.fatal


def test_dry_run_writes_a_report_and_changes_nothing(setup: dict[str, Any]) -> None:
    add_source(setup["settings"])
    registry_before = (setup["settings"].state_dir / "sources.json").read_text(encoding="utf-8")
    stats = run(setup, dry_run=True)
    assert dict(stats.posts) == {"would extract": 2}
    assert not day_file(setup["settings"].output_dir, TODAY).exists()
    assert not (setup["settings"].state_dir / "posts.json").exists()
    assert (setup["settings"].state_dir / "sources.json").read_text(encoding="utf-8") == registry_before
    report = json.loads((setup["settings"].output_dir / DRY_RUN_REPORT_FILE).read_text(encoding="utf-8"))
    assert [post["selected"] for post in report["posts"]] == [2, 2]
    assert report["posts"][0]["selector"] == ".entry-content"


def test_republished_posts_are_skipped(setup: dict[str, Any]) -> None:
    add_source(setup["settings"])
    extractor = FakeExtractor()
    stats = run(setup, extractor=extractor, pages=FakePages(upload_path="2015/12"))
    assert dict(stats.posts) == {"republished": 2} and extractor.usage.calls == 0
    assert SourceRegistry.load(setup["settings"].state_dir).get(BLOG).posts_processed == 0


def test_outfits_of_minors_are_not_stored(setup: dict[str, Any]) -> None:
    add_source(setup["settings"])
    stats = run(setup, extractor=FakeExtractor(age_group="Child"))
    assert stats.minors_skipped == 2 and stats.outfits == 0
    assert not day_file(setup["settings"].output_dir, TODAY).exists()
    assert SourceRegistry.load(setup["settings"].state_dir).get(BLOG).posts_with_outfits == 0


def test_probation_source_is_promoted(setup: dict[str, Any]) -> None:
    add_source(setup["settings"], status="probation", outcomes=[False])
    stats = run(setup)
    assert stats.source_transitions == [{"source": BLOG, "from": "probation", "to": "active"}]
    assert SourceRegistry.load(setup["settings"].state_dir).get(BLOG).status == "active"


def test_empty_registry_is_seeded_from_legacy_records(setup: dict[str, Any], tmp_path: Path) -> None:
    legacy_dir = tmp_path / "legacy"
    legacy_file = legacy_dir / "2026" / "07" / "fashion_analytics_2026-07-01.json"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text(json.dumps([{"source_url": f"https://{BLOG}/"}] * 6), encoding="utf-8")
    stats = run(setup, legacy_dir=legacy_dir)
    assert SourceRegistry.load(setup["settings"].state_dir).get(BLOG).status == "active"
    assert dict(stats.posts) == {"done": 2}
