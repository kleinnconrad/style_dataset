"""Tests for the Gemini extraction in src/extraction.py, with a fake client."""
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from google.genai import errors, types

from extraction import (
    MIN_CALL_INTERVAL_S,
    CallBudgetExceededError,
    FatalRequestError,
    GeminiExtractor,
    ImageInput,
    PostContext,
    QuotaExhaustedError,
    TransientExtractionError,
    UnusableResponseError,
    build_contents,
    classify_quota_error,
    extraction_hash,
)
from settings import Settings

FIXTURE = Path(__file__).parent / "fixtures" / "post_extraction.json"

POST = PostContext(
    title="Fall outfit",
    published_date="2026-10-08",
    tags=("Outfits", "Fall"),
    text="My new coat is from Everlane.",
    link_texts=("Shop the coat",),
)
IMAGES = [ImageInput(jpeg=f"jpeg-{n}".encode(), alt=f"Photo {n}") for n in range(1, 5)]


def fixture_payload() -> dict[str, Any]:
    """Returns a fresh copy of the fixture response."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def make_response(
    payload: Optional[dict[str, Any]] = None,
    *,
    text: Optional[str] = None,
    finish_reason: str = "STOP",
    block_reason: Optional[str] = None,
) -> SimpleNamespace:
    """Builds an object with the attributes of a GenerateContentResponse."""
    if payload is not None:
        text = json.dumps(payload)
    return SimpleNamespace(
        text=text,
        prompt_feedback=SimpleNamespace(block_reason=block_reason) if block_reason else None,
        candidates=[] if block_reason else [SimpleNamespace(finish_reason=finish_reason)],
        usage_metadata=SimpleNamespace(
            prompt_token_count=1000, candidates_token_count=200, thoughts_token_count=50, total_token_count=1250
        ),
    )


def quota_error(per_day: bool, retry_delay: Optional[str] = None) -> errors.ClientError:
    """Builds a 429 error as returned by the Gemini API."""
    quota_id = "GenerateRequestsPerDayPerProjectPerModel-FreeTier" if per_day else "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    details: list[dict[str, Any]] = [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]}
    ]
    if retry_delay:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    body = {"error": {"code": 429, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED", "details": details}}
    return errors.ClientError(429, body)


class FakeModels:
    """Returns prepared responses or raises prepared errors, in order."""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[list[Any]] = []

    def generate_content(self, *, model: str, contents: list[Any], config: Any) -> Any:
        self.requests.append(contents)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    """Test double for genai.Client."""

    def __init__(self, outcomes: list[Any]) -> None:
        self.models = FakeModels(outcomes)


def make_extractor(outcomes: list[Any], max_calls: int = 10) -> tuple[GeminiExtractor, FakeClient, list[float]]:
    """Creates an extractor with a fake client, a fixed clock and recorded sleeps."""
    client = FakeClient(outcomes)
    sleeps: list[float] = []
    settings = Settings(gemini_model="test-model", max_images_per_post=8, max_gemini_calls_per_run=max_calls)
    extractor = GeminiExtractor(settings, client=client, sleep=sleeps.append, clock=lambda: 100.0)
    return extractor, client, sleeps


def test_contents_alternate_labels_and_images_and_end_with_the_prompt() -> None:
    contents = build_contents(POST, IMAGES[:2])
    assert contents[0] == 'Image 1 (alt text: "Photo 1"):'
    assert isinstance(contents[1], types.Part)
    assert contents[1].inline_data.mime_type == "image/jpeg"
    assert contents[2] == 'Image 2 (alt text: "Photo 2"):'
    prompt = contents[-1]
    assert len(contents) == 5
    assert "The 2 images above" in prompt
    assert "Tags: Outfits, Fall" in prompt
    assert "My new coat is from Everlane." in prompt


def test_response_is_cleaned() -> None:
    extractor, client, _ = make_extractor([make_response(fixture_payload())])
    result = extractor.extract(POST, IMAGES)
    first, second = result.extraction.outfits
    assert first.image_indexes == [1, 2]
    assert first.style_detail == "Relaxed autumn minimalism"
    assert first.aesthetic_other is None
    assert first.aesthetic_tags == ["Quiet luxury/Old money"]
    assert first.garments[0].design_details == ["Buttons", "Pockets"]
    assert first.brand_mentions == ["Everlane"]
    assert second.focal_garment_index is None
    assert [item.index for item in result.extraction.rejected_images] == [3]
    assert result.unassigned_indexes == []
    assert not result.split
    assert extractor.usage.calls == 1
    assert extractor.usage.total_tokens == 1250


def test_generic_words_are_not_brands() -> None:
    payload = fixture_payload()
    payload["outfits"][0]["brand_mentions"] = ["JEANS", "Sweater", "Everlane", "Camel"]
    post = PostContext(title="Fall outfit", published_date=None, text="JEANS: Everlane. Sweater in camel.")
    extractor, _, _ = make_extractor([make_response(payload)])
    assert extractor.extract(post, IMAGES).extraction.outfits[0].brand_mentions == ["Everlane"]


def test_images_without_outfit_or_rejection_are_unassigned() -> None:
    payload = fixture_payload()
    payload["outfits"].pop()
    extractor, _, _ = make_extractor([make_response(payload)])
    result = extractor.extract(POST, IMAGES)
    assert result.unassigned_indexes == [4]


def half_payloads() -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns responses for images 1-2 and 3-4 of a split request, numbered per half."""
    first_half = fixture_payload()
    first_half["outfits"] = first_half["outfits"][:1]
    first_half["outfits"][0]["image_indexes"] = [1, 2]
    first_half["rejected_images"] = []
    second_half = fixture_payload()
    second_half["outfits"] = second_half["outfits"][1:]
    second_half["outfits"][0]["image_indexes"] = [2]
    second_half["rejected_images"] = [{"index": 1, "reason": "Product/flat lay"}]
    return first_half, second_half


def test_blocked_response_is_retried_with_the_images_split() -> None:
    first_half, second_half = half_payloads()
    extractor, client, _ = make_extractor([
        make_response(block_reason="SAFETY"),
        make_response(first_half),
        make_response(second_half),
    ])
    result = extractor.extract(POST, IMAGES)
    assert result.split
    assert [outfit.image_indexes for outfit in result.extraction.outfits] == [[1, 2], [4]]
    assert [item.index for item in result.extraction.rejected_images] == [3]
    assert [len(request) for request in client.models.requests] == [9, 5, 5]


def test_failed_half_is_reported_as_unusable() -> None:
    first_half, _ = half_payloads()
    extractor, _, _ = make_extractor([
        make_response(text="not json"),
        make_response(first_half),
        make_response(fixture_payload(), finish_reason="SAFETY"),
    ])
    result = extractor.extract(POST, IMAGES)
    assert result.unusable_indexes == [3, 4]
    assert result.unassigned_indexes == []
    assert len(result.extraction.outfits) == 1


def test_both_halves_unusable_raises() -> None:
    extractor, _, _ = make_extractor([make_response(block_reason="OTHER")] * 3)
    with pytest.raises(UnusableResponseError):
        extractor.extract(POST, IMAGES)


def test_single_image_is_not_split() -> None:
    extractor, _, _ = make_extractor([make_response(text="")])
    with pytest.raises(UnusableResponseError):
        extractor.extract(POST, IMAGES[:1])
    assert extractor.usage.calls == 1


def test_per_day_quota_stops_the_run() -> None:
    extractor, _, _ = make_extractor([quota_error(per_day=True)])
    with pytest.raises(QuotaExhaustedError):
        extractor.extract(POST, IMAGES)
    assert extractor.usage.calls == 1


def test_per_minute_quota_waits_and_retries() -> None:
    extractor, _, sleeps = make_extractor([quota_error(per_day=False, retry_delay="7s"), make_response(fixture_payload())])
    result = extractor.extract(POST, IMAGES)
    assert len(result.extraction.outfits) == 2
    assert 7.0 in sleeps
    assert extractor.usage.calls == 2


def test_per_minute_quota_gives_up_after_three_waits() -> None:
    extractor, _, _ = make_extractor([quota_error(per_day=False)] * 4)
    with pytest.raises(QuotaExhaustedError):
        extractor.extract(POST, IMAGES)
    assert extractor.usage.calls == 4


def test_call_budget_stops_the_run() -> None:
    extractor, _, _ = make_extractor([make_response(block_reason="SAFETY")], max_calls=1)
    with pytest.raises(CallBudgetExceededError):
        extractor.extract(POST, IMAGES)


def test_client_error_other_than_429_is_fatal() -> None:
    error = errors.ClientError(400, {"error": {"code": 400, "message": "Invalid schema", "status": "INVALID_ARGUMENT"}})
    extractor, _, _ = make_extractor([error])
    with pytest.raises(FatalRequestError):
        extractor.extract(POST, IMAGES)


def test_server_error_is_transient() -> None:
    error = errors.ServerError(503, {"error": {"code": 503, "message": "Overloaded", "status": "UNAVAILABLE"}})
    extractor, _, _ = make_extractor([error])
    with pytest.raises(TransientExtractionError):
        extractor.extract(POST, IMAGES)


def test_calls_keep_the_minimum_interval() -> None:
    extractor, _, sleeps = make_extractor([make_response(fixture_payload()), make_response(fixture_payload())])
    extractor.extract(POST, IMAGES)
    extractor.extract(POST, IMAGES)
    # The fake clock does not advance, so the second call waits the full interval
    assert sleeps == [MIN_CALL_INTERVAL_S]


def test_too_many_images_are_rejected() -> None:
    extractor, _, _ = make_extractor([])
    with pytest.raises(ValueError):
        extractor.extract(POST, IMAGES * 3)


def test_classify_quota_error_reads_retry_delay() -> None:
    assert classify_quota_error(quota_error(per_day=False, retry_delay="1.5s")) == (False, 1.5)
    assert classify_quota_error(quota_error(per_day=True)) == (True, None)


def test_extraction_hash_depends_on_the_model() -> None:
    first = extraction_hash(Settings(gemini_model="model-a"))
    assert first == extraction_hash(Settings(gemini_model="model-a"))
    assert first != extraction_hash(Settings(gemini_model="model-b"))
