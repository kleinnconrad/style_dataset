"""Tests for the source registry and its lifecycle rules in src/sources.py."""
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import pytest

import sources as sources_module
from feeds import parse_feed
from sources import (
    RECENT_WINDOW,
    SourceEntry,
    SourceRegistry,
    apply_rules,
    collect_legacy_domains,
    evaluate_candidate,
    infer_country,
    language_code,
    record_feed_result,
    record_post_outcome,
    seed_from_legacy,
)

TODAY = date(2026, 10, 10)
FEEDS_DIR = Path(__file__).parent / "fixtures" / "feeds"


def source(status: str = "active", **fields: Any) -> SourceEntry:
    """Builds a source with recent activity; ``fields`` override the defaults."""
    values: dict[str, Any] = {
        "source_id": "example.com", "homepage": "https://example.com/", "status": status,
        "status_since": TODAY - timedelta(days=30), "origin": "legacy", "added": TODAY - timedelta(days=60),
        "last_post_date": TODAY - timedelta(days=3), "posts_last_60_days": 8,
    }
    values.update(fields)
    return SourceEntry(**values)


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (source("probation", recent_outcomes=[True, True]), "active"),
        (source("probation", recent_outcomes=[True, False, False]), "probation"),
        (source("probation", recent_outcomes=[False, True, False, False]), "rejected"),
        (source("probation", recent_outcomes=[False, False, False]), "rejected"),
        (source("active", recent_outcomes=[True, True] + [False] * 10), "retired"),
        (source("active", recent_outcomes=[True, True, True] + [False] * 9), "active"),
        (source("active", recent_outcomes=[False] * 11), "active"),
        (source("active", last_post_date=TODAY - timedelta(days=91)), "dormant"),
        (source("probation", last_post_date=TODAY - timedelta(days=91)), "dormant"),
        (source("dormant", previous_status="active", posts_last_60_days=2), "active"),
        (source("dormant", previous_status="probation", posts_last_60_days=3), "probation"),
        (source("dormant", previous_status="active", posts_last_60_days=1), "dormant"),
        (source("active", consecutive_feed_failures=7), "broken"),
        (source("dormant", previous_status="active", posts_last_60_days=0, consecutive_feed_failures=7), "broken"),
        (source("broken", previous_status="active", consecutive_feed_failures=0), "active"),
        (source("broken", previous_status="active", consecutive_feed_failures=9), "broken"),
        (source("rejected", consecutive_feed_failures=9), "rejected"),
        (source("retired", posts_last_60_days=20), "retired"),
    ],
)
def test_lifecycle_rules(entry: SourceEntry, expected: str) -> None:
    assert apply_rules(entry, TODAY).status == expected


def test_paused_sources_remember_their_status() -> None:
    dormant = apply_rules(source("active", last_post_date=TODAY - timedelta(days=120)), TODAY)
    assert (dormant.previous_status, dormant.status_since) == ("active", TODAY)
    assert "90 days" in dormant.note
    revived = apply_rules(dormant.model_copy(update={"posts_last_60_days": 2}), TODAY)
    assert (revived.status, revived.previous_status) == ("active", None)


def test_post_outcomes_keep_a_window() -> None:
    entry = source(recent_outcomes=[True] * RECENT_WINDOW, posts_processed=12, posts_with_outfits=12)
    updated = record_post_outcome(entry, produced_outfit=False)
    assert len(updated.recent_outcomes) == RECENT_WINDOW
    assert updated.recent_outcomes[-1] is False
    assert (updated.posts_processed, updated.posts_with_outfits) == (13, 12)


def test_feed_results_update_the_statistics() -> None:
    feed = parse_feed((FEEDS_DIR / "wordpress_rss.xml").read_bytes(), "https://www.example-style.com/feed/")
    entry = source(consecutive_feed_failures=3, last_post_date=None, posts_last_60_days=0, language=None)
    updated = record_feed_result(entry, feed, TODAY)
    assert updated.consecutive_feed_failures == 0
    assert updated.last_post_date == date(2026, 10, 8)
    assert updated.posts_last_60_days == 3
    assert updated.language == "en"
    assert (updated.country, updated.country_basis) == ("GB", "feed_language")
    failed = record_feed_result(updated, None, TODAY)
    assert failed.consecutive_feed_failures == 1


def test_model_country_is_not_overwritten_by_the_feed() -> None:
    feed = parse_feed((FEEDS_DIR / "wordpress_rss.xml").read_bytes(), "https://www.example-style.com/feed/")
    entry = source(country="US", country_basis="model")
    assert record_feed_result(entry, feed, TODAY).country == "US"


@pytest.mark.parametrize(
    ("source_id", "feed_language", "expected"),
    [
        ("blog.co.uk", None, ("GB", "domain")),
        ("modeblog.de", "en-US", ("DE", "domain")),
        ("style.co", "en-GB", ("GB", "feed_language")),
        ("example.com", "en-us", ("US", "feed_language")),
        ("example.io", "fr_FR", ("FR", "feed_language")),
        ("example.com", "en", (None, "unknown")),
        ("example.com", None, (None, "unknown")),
    ],
)
def test_infer_country(source_id: str, feed_language: Optional[str], expected: tuple) -> None:
    assert infer_country(source_id, feed_language) == expected


@pytest.mark.parametrize(
    ("tag", "expected"),
    [("en-GB", "en"), ("de", "de"), ("en_US", "en"), (None, None), ("", None), ("x-klingon", None)],
)
def test_language_code(tag: Optional[str], expected: Optional[str]) -> None:
    assert language_code(tag) == expected


def recent_feed(rss: Any, domain: str, days_ago: list[int]) -> bytes:
    """Builds a feed of a domain with posts ``days_ago`` before TODAY."""
    return rss([(f"Post {d}", f"https://{domain}/post-{d}/", TODAY - timedelta(days=d)) for d in days_ago])


def test_evaluate_candidate_statuses(web: Any, rss: Any) -> None:
    web.add_feed("https://busy.example/feed/", recent_feed(rss, "busy.example", [2, 9, 20]))
    web.add_feed("https://quiet.example/feed/", recent_feed(rss, "quiet.example", [200, 400]))

    assert evaluate_candidate("https://busy.example/", "legacy", TODAY, web, legacy_records=6).status == "active"
    assert evaluate_candidate("https://busy.example/", "legacy", TODAY, web, legacy_records=2).status == "probation"
    assert evaluate_candidate("https://busy.example/", "discovery", TODAY, web).status == "probation"
    quiet = evaluate_candidate("https://quiet.example/", "legacy", TODAY, web, legacy_records=6)
    assert (quiet.status, quiet.previous_status) == ("dormant", "active")


@pytest.mark.parametrize(
    ("setup", "status", "note"),
    [
        ("no feed", "rejected", "no feed found"),
        ("blocked", "rejected", "site blocks automated requests (HTTP 403)"),
        ("gone", "rejected", "site not found (HTTP 404)"),
        ("unreachable", "broken", "site not reachable"),
        ("server error", "broken", "site not reachable (HTTP 503)"),
    ],
)
def test_candidates_without_feed(web: Any, setup: str, status: str, note: str) -> None:
    homepage = "https://candidate.example/"
    if setup == "no feed":
        web.add(homepage, b"<html><body>A blog without feed</body></html>")
    elif setup == "blocked":
        web.add(homepage, b"Access denied", status=403)
    elif setup == "unreachable":
        web.unreachable.add(homepage)
    elif setup == "server error":
        web.add(homepage, b"Service unavailable", status=503)
    entry = evaluate_candidate(homepage, "legacy", TODAY, web, legacy_records=9)
    assert (entry.status, entry.note) == (status, note)


def test_unreachable_source_returns_once_its_feed_works(web: Any, rss: Any) -> None:
    web.unreachable.add("https://flaky.example/")
    broken = evaluate_candidate("https://flaky.example/", "legacy", TODAY, web, legacy_records=9)
    assert (broken.status, broken.previous_status, broken.consecutive_feed_failures) == ("broken", "active", 1)
    assert apply_rules(broken, TODAY).status == "broken"
    feed = parse_feed(recent_feed(rss, "flaky.example", [1, 2]), "https://flaky.example/feed/")
    assert apply_rules(record_feed_result(broken, feed, TODAY), TODAY).status == "active"


def test_admission_respects_the_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources_module, "MAX_ADMITTED_SOURCES", 2)
    registry = SourceRegistry.load(tmp_path)
    assert registry.admit(source("active", source_id="a.example"))
    assert registry.admit(source("probation", source_id="b.example"))
    assert not registry.admit(source("probation", source_id="c.example"))
    assert registry.admit(source("dormant", source_id="d.example"))
    assert "c.example" not in registry and "d.example" in registry


def write_legacy(data_dir: Path, name: str, urls: list[str]) -> None:
    """Writes a legacy records file with one record per URL."""
    path = data_dir / "2026" / "07" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([{"source_url": url, "clothing_style": "Casual"} for url in urls]), encoding="utf-8")


def test_legacy_domains_merge_www_variants(tmp_path: Path) -> None:
    write_legacy(tmp_path, "fashion_analytics_2026-07-01.json",
                 ["https://www.alpha.example/"] * 4 + ["https://alpha.example/blog"] * 2)
    domains = collect_legacy_domains(tmp_path)
    assert domains["alpha.example"].records == 6
    assert domains["alpha.example"].homepage == "https://www.alpha.example/"


def test_seed_from_legacy(tmp_path: Path, web: Any, rss: Any) -> None:
    data_dir = tmp_path / "data"
    write_legacy(data_dir, "fashion_analytics_2026-07-01.json",
                 ["https://www.alpha.example/"] * 6 + ["https://beta.example/blog"] * 2)
    write_legacy(data_dir, "fashion_analytics_2026-07-02.json",
                 ["https://gamma.example/"] * 7 + ["https://delta.example/"])
    web.add_feed("https://www.alpha.example/feed/", recent_feed(rss, "alpha.example", [1, 8]))
    web.add_feed("https://beta.example/feed/", recent_feed(rss, "beta.example", [3, 4, 5]))
    web.add_feed("https://gamma.example/feed/", recent_feed(rss, "gamma.example", [300]))

    registry = SourceRegistry.load(tmp_path / "state")
    summary = seed_from_legacy(registry, data_dir, TODAY, web, workers=2)
    assert dict(summary) == {"active": 1, "probation": 1, "dormant": 1, "rejected": 1}
    assert registry.get("alpha.example").legacy_records == 6
    assert registry.get("gamma.example").previous_status == "active"

    registry.save()
    reloaded = SourceRegistry.load(tmp_path / "state")
    assert {e.source_id: e.model_dump() for e in reloaded} == {e.source_id: e.model_dump() for e in registry}
