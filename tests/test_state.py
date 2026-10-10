"""Tests for the persistent state in src/state.py."""
import json
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

import state as state_module
from state import MAX_ATTEMPTS, PipelineState, atomic_write_text, dump_entries, load_entries

TODAY = date(2026, 10, 10)


def register(state: PipelineState, key: str, first_seen: date = TODAY) -> None:
    """Registers a post with fixed metadata."""
    state.register(key, source_id="example.com", url=key + "/", title="Outfit", published=first_seen,
                   tags=("Outfits",), today=first_seen)


def test_round_trip_and_file_format(tmp_path: Path) -> None:
    state = PipelineState.load(tmp_path)
    register(state, "https://example.com/a")
    register(state, "https://example.com/b")
    state.complete("https://example.com/a", "done", today=TODAY, outfits=2,
                   image_hashes=["00000000000000ff"], canonical_url="https://example.com/a/")
    state.fail("https://example.com/b", "HTTP 404", counts_as_attempt=True, today=TODAY)
    state.save()

    lines = (tmp_path / "posts.json").read_text(encoding="utf-8").splitlines()
    assert lines[:3] == ["{", '"version": 1,', '"posts": {']
    assert len(lines) == 2 + 5  # one line per entry plus five lines of frame
    assert json.loads((tmp_path / "images.json").read_text(encoding="utf-8"))["version"] == 1

    loaded = PipelineState.load(tmp_path)
    assert dict(loaded.posts) == dict(state.posts)
    assert loaded.get("https://example.com/a").outfits == 2
    assert loaded.find_similar_image("00000000000000ff") == "00000000000000ff"


def test_missing_files_mean_empty_state(tmp_path: Path) -> None:
    state = PipelineState.load(tmp_path / "nothing-here")
    assert len(state.posts) == 0


def test_other_file_version_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "posts.json").write_text(dump_entries("posts", {}, version=99), encoding="utf-8")
    with pytest.raises(ValueError):
        load_entries(tmp_path / "posts.json", "posts")


def test_counted_failures_give_up_after_max_attempts(tmp_path: Path) -> None:
    state = PipelineState(tmp_path, {}, {})
    register(state, "https://example.com/a")
    for _ in range(5):
        entry = state.fail("https://example.com/a", "server error", counts_as_attempt=False, today=TODAY)
    assert (entry.status, entry.attempts) == ("pending", 0)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        entry = state.fail("https://example.com/a", "blocked", counts_as_attempt=True, today=TODAY)
        assert entry.attempts == attempt
    assert entry.status == "failed"


def test_is_due_and_expiry(tmp_path: Path) -> None:
    state = PipelineState(tmp_path, {}, {})
    register(state, "https://example.com/recent", first_seen=TODAY - timedelta(days=5))
    register(state, "https://example.com/stale", first_seen=TODAY - timedelta(days=31))
    register(state, "https://example.com/done")
    state.complete("https://example.com/done", "no_images", today=TODAY)
    assert state.is_due("https://example.com/new", TODAY, 30)
    assert state.is_due("https://example.com/recent", TODAY, 30)
    assert not state.is_due("https://example.com/stale", TODAY, 30)
    assert not state.is_due("https://example.com/done", TODAY, 30)
    assert state.expire_pending(TODAY, 30) == 1
    assert state.get("https://example.com/stale").status == "failed"


def test_near_duplicate_images_are_found(tmp_path: Path) -> None:
    state = PipelineState(tmp_path, {}, {})
    state.add_images(["0000000000000000"], "https://example.com/a", TODAY)
    five_bits = f"{0b11111:016x}"
    six_bits = f"{0b111111:016x}"
    assert state.find_similar_image(five_bits) == "0000000000000000"
    assert state.find_similar_image(six_bits) is None
    with pytest.raises(ValueError):
        state.add_images(["abc"], "https://example.com/a", TODAY)


def test_complete_only_accepts_final_statuses(tmp_path: Path) -> None:
    state = PipelineState(tmp_path, {}, {})
    register(state, "https://example.com/a")
    with pytest.raises(ValueError):
        state.complete("https://example.com/a", "pending", today=TODAY)
    with pytest.raises(KeyError):
        state.complete("https://example.com/unknown", "done", today=TODAY)


def test_failed_write_keeps_the_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "posts.json"
    atomic_write_text(target, "old")

    def failing_replace(source: str, destination: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(state_module.os, "replace", failing_replace)
    with pytest.raises(OSError):
        atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert [path.name for path in tmp_path.iterdir()] == ["posts.json"]
    assert os.path.exists(target)
