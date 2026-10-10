"""Tests for the daily records files in src/storage.py."""
import json
from datetime import date
from pathlib import Path

from extraction import clean_extraction
from records import PostMetadata, SourceMetadata, build_records
from schema import ImageRef, PostExtraction
from storage import append_records, day_file, load_day

FIXTURE = Path(__file__).parent / "fixtures" / "post_extraction.json"
DAY = date(2026, 10, 10)


def records_for(post_url: str, hash_offset: int = 0) -> list:
    """Builds the two fixture records for a post; ``hash_offset`` changes the image hashes."""
    extraction, _ = clean_extraction(PostExtraction.model_validate_json(FIXTURE.read_text(encoding="utf-8")),
                                     image_count=4, reference_text="Everlane")
    images = [ImageRef(hash=f"{n + hash_offset:016x}", width=800, height=1200) for n in range(1, 5)]
    post = PostMetadata(url=post_url, canonical_url=None, title="Look", published_date="2026-10-08",
                        tags=(), shopping_link_count=0)
    source = SourceMetadata(source_id="example.com", country="US", country_basis="domain", language="en")
    return build_records(extraction, images, post, source, model="m", extraction_hash="h", date_scraped="2026-10-10")


def test_day_file_path(tmp_path: Path) -> None:
    assert day_file(tmp_path, DAY) == tmp_path / "2026" / "10" / "fashion_analytics_2026-10-10.json"


def test_appending_the_same_records_twice_keeps_one_copy(tmp_path: Path) -> None:
    records = records_for("https://example.com/look/")
    append_records(tmp_path, DAY, records)
    append_records(tmp_path, DAY, records)
    assert [r["record_id"] for r in load_day(day_file(tmp_path, DAY))] == [r.record_id for r in records]


def test_records_of_a_reprocessed_post_are_replaced(tmp_path: Path) -> None:
    first_attempt = records_for("https://example.com/look/")
    other_post = records_for("https://example.com/other/", hash_offset=100)
    append_records(tmp_path, DAY, first_attempt)
    append_records(tmp_path, DAY, other_post)
    # The second attempt returns different hashes and therefore different record ids
    second_attempt = records_for("https://www.example.com/look/?utm_source=rss", hash_offset=50)
    append_records(tmp_path, DAY, second_attempt, post_key="https://example.com/look")
    stored = load_day(day_file(tmp_path, DAY))
    assert {r["record_id"] for r in stored} == {r.record_id for r in other_post + second_attempt}


def test_legacy_records_in_the_day_file_are_kept(tmp_path: Path) -> None:
    path = day_file(tmp_path, DAY)
    path.parent.mkdir(parents=True)
    legacy = [{"source_url": "https://example.com/", "clothing_style": "Casual", "date_scraped": "2026-10-10"}]
    path.write_text(json.dumps(legacy), encoding="utf-8")
    append_records(tmp_path, DAY, records_for("https://example.com/look/"), post_key="https://example.com/look")
    stored = load_day(path)
    assert stored[0] == legacy[0]
    assert len(stored) == 3


def test_nothing_is_written_without_changes(tmp_path: Path) -> None:
    assert append_records(tmp_path, DAY, [], post_key="https://example.com/look") is None
    assert not day_file(tmp_path, DAY).exists()
