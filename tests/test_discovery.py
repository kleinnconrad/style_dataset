"""Tests for the weekly maintenance in src/discovery.py, with a fake search model and a fake web."""
import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import errors

import discovery as discovery_module
from discovery import (
    SearchAnswer,
    SearchModel,
    discover_sources,
    lookup_countries,
    parse_json_array,
    recheck_paused_sources,
    weekly_focus,
    weekly_maintenance,
)
from extraction import CallBudgetExceededError, FatalRequestError, QuotaExhaustedError, TransientExtractionError
from settings import Settings
from sources import SourceEntry, SourceRegistry
from state import RUN_LOG_FILE

TODAY = date(2026, 10, 11)


class FakeSearchModel:
    """Returns prepared answers in order and records the prompts.

    An answer is an exception to raise, a ``SearchAnswer``, a text, or a value sent as JSON text.
    """

    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.calls = 0

    def ask(self, prompt: str, temperature: float) -> SearchAnswer:
        self.calls += 1
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, SearchAnswer):
            return answer
        return SearchAnswer(text=answer if isinstance(answer, str) else json.dumps(answer))


def entry(source_id: str, status: str = "active", **fields: Any) -> SourceEntry:
    """Builds a registry entry."""
    values: dict[str, Any] = {"source_id": source_id, "homepage": f"https://{source_id}/", "status": status,
                              "status_since": TODAY - timedelta(days=20), "origin": "legacy",
                              "added": TODAY - timedelta(days=20), "feed_url": f"https://{source_id}/feed/"}
    values.update(fields)
    return SourceEntry(**values)


def registry_with(tmp_path: Path, *entries: SourceEntry) -> SourceRegistry:
    """Builds a registry in a temporary folder."""
    registry = SourceRegistry.load(tmp_path)
    for item in entries:
        registry.put(item)
    return registry


def recent_feed(rss: Any, domain: str, days_ago: list[int]) -> bytes:
    """Builds a feed with posts ``days_ago`` before TODAY."""
    return rss([(f"Outfit {d}", f"https://{domain}/outfit-{d}/", TODAY - timedelta(days=d)) for d in days_ago])


@pytest.mark.parametrize(
    "text",
    ['[{"a": 1}]', 'Here you go:\n```json\n[{"a": 1}]\n```', 'Answer: [{"a": 1}] Hope this helps.'],
)
def test_parse_json_array(text: str) -> None:
    assert parse_json_array(text) == [{"a": 1}]


@pytest.mark.parametrize("text", ["No blogs found.", "[not json]", ""])
def test_parse_json_array_rejects_other_answers(text: str) -> None:
    with pytest.raises(ValueError):
        parse_json_array(text)


def test_focus_changes_every_week() -> None:
    monday = TODAY - timedelta(days=TODAY.weekday())
    assert weekly_focus(monday) == weekly_focus(monday + timedelta(days=6))
    assert weekly_focus(monday) != weekly_focus(monday + timedelta(days=7))


def test_country_lookup_sets_model_countries_once(tmp_path: Path) -> None:
    registry = registry_with(
        tmp_path,
        entry("alpha.example", country="US", country_basis="feed_language"),
        entry("beta.example", status="dormant"),
        entry("gamma.example", country="FR", country_basis="model"),
        entry("delta.example", status="rejected"),
    )
    model = FakeSearchModel([[
        {"domain": "alpha.example", "country": "gb", "language": "en"},
        {"domain": "www.beta.example", "country": "Germany", "language": "de"},
    ]])
    assert lookup_countries(registry, model, TODAY) == 1
    assert "gamma.example" not in model.prompts[0] and "delta.example" not in model.prompts[0]
    alpha, beta = registry.get("alpha.example"), registry.get("beta.example")
    assert (alpha.country, alpha.country_basis, alpha.language) == ("GB", "model", "en")
    # "Germany" is not an ISO code: the country stays unknown, but the lookup is recorded
    assert (beta.country, beta.country_basis, beta.language, beta.country_lookup) == (None, "unknown", "de", TODAY)
    assert lookup_countries(registry, FakeSearchModel([]), TODAY + timedelta(days=7)) == 0


def test_failed_lookup_is_retried_next_time(tmp_path: Path) -> None:
    registry = registry_with(tmp_path, entry("alpha.example"))
    lookup_countries(registry, FakeSearchModel(["Sorry, I cannot help."]), TODAY)
    assert registry.get("alpha.example").country_lookup is None


def test_discovery_admits_and_records_candidates(tmp_path: Path, web: Any, rss: Any) -> None:
    registry = registry_with(tmp_path, entry("known.example"))
    # The proposed post links the blog's feed
    web.add("https://new.example/2026/10/fall-look/",
            b'<html><head><link rel="alternate" type="application/rss+xml" href="/blog/feed/"></head></html>')
    web.add_feed("https://new.example/blog/feed/", recent_feed(rss, "new.example", [3, 10]))
    web.add("https://nofeed.example/", b"<html><body>Blog without feed</body></html>")
    model = FakeSearchModel([[
        {"post_url": "https://new.example/2026/10/fall-look/", "country": "SE", "language": "sv"},
        {"homepage": "https://www.known.example/", "country": "US"},
        {"homepage": "https://www.instagram.com/someone/"},
        {"homepage": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"},
        {"homepage": "https://nofeed.example/"},
        {"homepage": "not a url"},
    ]])
    summary = discover_sources(registry, model, TODAY, web)
    assert dict(summary) == {"probation": 1, "known": 1, "not a blog": 2, "rejected": 1, "invalid url": 1}
    new = registry.get("new.example")
    assert (new.status, new.origin, new.country, new.country_basis, new.language) == (
        "probation", "discovery", "SE", "model", "sv")
    assert (new.homepage, new.feed_url) == ("https://new.example/", "https://new.example/blog/feed/")
    assert registry.get("nofeed.example").note == "no feed found"
    assert "known.example" in model.prompts[0]
    assert "October 2026 or September 2026" in model.prompts[0]


def test_search_sources_are_candidates_without_a_list(tmp_path: Path, web: Any, rss: Any) -> None:
    registry = registry_with(tmp_path, entry("known.example"))
    web.add_feed("https://grounded.example/feed/", recent_feed(rss, "grounded.example", [2, 9]))
    answer = SearchAnswer(text="I could not find posts from these months.",
                          source_domains=("grounded.example", "vogue.com", "known.example"))
    summary = discover_sources(registry, FakeSearchModel([answer]), TODAY, web)
    assert dict(summary) == {"answer without list": 1, "from search sources": 3, "probation": 1,
                             "not a blog": 1, "known": 1}
    assert registry.get("grounded.example").country_basis != "model"


def test_source_domains_come_from_the_grounding_metadata() -> None:
    def chunk(title: Any) -> SimpleNamespace:
        return SimpleNamespace(web=SimpleNamespace(domain=None, title=title, uri="https://vertexaisearch.cloud.google.com/x"))

    metadata = SimpleNamespace(grounding_chunks=[chunk("WWW.Petite-Style.com"), chunk("My October outfits"),
                                                 chunk("petite-style.com"), SimpleNamespace(web=None), chunk(None)])
    response = SimpleNamespace(candidates=[SimpleNamespace(grounding_metadata=metadata)])
    assert discovery_module._source_domains(response) == ("petite-style.com",)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("gb", "GB"), ("UK", "GB"), ("EL", "GR"), ("Germany", None), ("U1", None), (None, None)],
)
def test_valid_country(answer: Any, expected: Any) -> None:
    assert discovery_module.valid_country(answer) == expected


def test_discovery_respects_the_weekly_limit(tmp_path: Path, web: Any, rss: Any,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery_module, "MAX_ADMISSIONS_PER_WEEK", 1)
    registry = registry_with(tmp_path)
    for name in ("one.example", "two.example"):
        web.add_feed(f"https://{name}/feed/", recent_feed(rss, name, [2, 5]))
    model = FakeSearchModel([[{"homepage": "https://one.example/"}, {"homepage": "https://two.example/"}]])
    summary = discover_sources(registry, model, TODAY, web)
    assert dict(summary) == {"probation": 1, "weekly limit reached": 1}
    assert "two.example" not in registry


def test_discovery_is_skipped_when_the_registry_is_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery_module, "MAX_ADMITTED_SOURCES", 1)
    model = FakeSearchModel([])
    summary = discover_sources(registry_with(tmp_path, entry("a.example")), model, TODAY)
    assert dict(summary) == {"registry full": 1} and model.calls == 0


def test_paused_sources_return_when_they_publish_again(tmp_path: Path, web: Any, rss: Any) -> None:
    registry = registry_with(
        tmp_path,
        entry("sleepy.example", status="dormant", previous_status="active"),
        entry("flaky.example", status="broken", previous_status="probation", feed_url=None,
              consecutive_feed_failures=1),
        entry("quiet.example", status="dormant", previous_status="active"),
    )
    web.add_feed("https://sleepy.example/feed/", recent_feed(rss, "sleepy.example", [4, 12]))
    web.add_feed("https://flaky.example/feed/", recent_feed(rss, "flaky.example", [1, 2]))
    web.add_feed("https://quiet.example/feed/", recent_feed(rss, "quiet.example", [150]))
    changes = recheck_paused_sources(registry, TODAY, web)
    assert sorted(changes) == [("flaky.example", "broken", "probation"), ("sleepy.example", "dormant", "active")]
    assert registry.get("flaky.example").feed_url == "https://flaky.example/feed/"
    assert registry.get("quiet.example").status == "dormant"


def test_weekly_maintenance_saves_and_logs(tmp_path: Path, web: Any, rss: Any) -> None:
    settings = Settings(output_dir=tmp_path)
    registry_with(settings.state_dir, entry("alpha.example"), entry("beta.example", status="dormant")).save()
    web.add_feed("https://beta.example/feed/", recent_feed(rss, "beta.example", [200]))
    model = FakeSearchModel([[{"domain": "alpha.example", "country": "AU"}], []])
    summary = weekly_maintenance(settings, TODAY, model, web)
    assert summary["countries_set"] == 1 and summary["candidates"] == {} and summary["gemini_calls"] == 2
    assert SourceRegistry.load(settings.state_dir).get("alpha.example").country == "AU"
    log = (settings.state_dir / RUN_LOG_FILE).read_text(encoding="utf-8").splitlines()
    assert json.loads(log[-1])["mode"] == "maintenance"


def test_quota_stop_keeps_the_rechecked_registry(tmp_path: Path, web: Any, rss: Any) -> None:
    settings = Settings(output_dir=tmp_path)
    registry_with(settings.state_dir, entry("sleepy.example", status="dormant", previous_status="active")).save()
    web.add_feed("https://sleepy.example/feed/", recent_feed(rss, "sleepy.example", [1, 3]))
    summary = weekly_maintenance(settings, TODAY, FakeSearchModel([QuotaExhaustedError("quota")]), web)
    assert summary["stopped_early"].startswith("QuotaExhaustedError") and not summary["fatal"]
    assert SourceRegistry.load(settings.state_dir).get("sleepy.example").status == "active"


class FakeClient:
    """Test double for genai.Client that raises a prepared error or returns a text."""

    def __init__(self, outcome: Any) -> None:
        self.models = SimpleNamespace(generate_content=self.generate_content)
        self.outcome = outcome

    def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return SimpleNamespace(text=self.outcome)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}),
         QuotaExhaustedError),
        (errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}}),
         FatalRequestError),
        (errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}}),
         TransientExtractionError),
    ],
)
def test_search_model_maps_api_errors(outcome: Exception, expected: type) -> None:
    model = SearchModel(Settings(), client=FakeClient(outcome), sleep=lambda s: None)
    with pytest.raises(expected):
        model.ask("prompt", 0.0)


def test_search_model_call_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery_module, "MAX_DISCOVERY_CALLS", 1)
    sleeps: list[float] = []
    model = SearchModel(Settings(), client=FakeClient("[]"), sleep=sleeps.append)
    assert model.ask("prompt", 0.0) == SearchAnswer(text="[]")
    with pytest.raises(CallBudgetExceededError):
        model.ask("prompt", 0.0)
    assert sleeps == []
