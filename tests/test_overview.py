"""Tests for the README overview in src/generate_dataset_overview.py."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

from extraction import clean_extraction
from generate_dataset_overview import END_MARKER, START_MARKER, build_overview, update_readme
from records import PostMetadata, SourceMetadata, build_records
from schema import ImageRef, PostExtraction
from sources import SourceEntry, SourceRegistry
from storage import append_records

FIXTURE = Path(__file__).parent / "fixtures" / "post_extraction.json"
NOW = datetime(2026, 10, 11, 1, 30, tzinfo=timezone.utc)


def write_records(data_dir: Path, post_url: str, first_hash: int) -> None:
    """Stores the two fixture records of a post; the image hashes start at ``first_hash``."""
    extraction, _ = clean_extraction(PostExtraction.model_validate_json(FIXTURE.read_text(encoding="utf-8")),
                                     image_count=4, reference_text="Everlane")
    images = [ImageRef(hash=f"{first_hash + n:016x}", width=800, height=1200) for n in range(4)]
    post = PostMetadata(url=post_url, canonical_url=None, title="Look", published_date="2026-10-08",
                        tags=(), shopping_link_count=1)
    source = SourceMetadata(source_id="example.com", country="US", country_basis="domain", language="en")
    records = build_records(extraction, images, post, source, model="m", extraction_hash="h",
                            date_scraped="2026-10-10")
    append_records(data_dir, date(2026, 10, 10), records)


def make_dataset(data_dir: Path) -> None:
    """Writes legacy records, schema 2.0 records, a registry and a post state file."""
    legacy = data_dir / "2026" / "06" / "fashion_analytics_2026-06-14.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps([{"source_url": "https://old.example/", "clothing_style": "Casual"}] * 2),
                      encoding="utf-8")
    write_records(data_dir, "https://example.com/look-one/", first_hash=0)
    # The second post repeats the last image of the first post
    write_records(data_dir, "https://example.com/look-two/", first_hash=3)
    registry = SourceRegistry.load(data_dir / "state")
    for source_id, status in (("example.com", "active"), ("old.example", "dormant")):
        registry.put(SourceEntry(source_id=source_id, homepage=f"https://{source_id}/", status=status,
                                 status_since=date(2026, 10, 1), origin="legacy", added=date(2026, 10, 1),
                                 country="US", posts_processed=2, posts_with_outfits=2,
                                 last_post_date=date(2026, 10, 8)))
    registry.save()
    (data_dir / "state" / "posts.json").write_text('{"version": 1, "posts": {}}', encoding="utf-8")


def test_overview_summarizes_both_schemas_and_the_registry(tmp_path: Path) -> None:
    make_dataset(tmp_path)
    overview = build_overview(tmp_path, NOW)
    assert "**Last updated:** 2026-10-11 01:30 UTC" in overview
    assert "**Schema 2.0 records:** 4 outfits from 2 posts of 1 sources, published 2026-10-08 to 2026-10-08" in overview
    assert "**Images reused across posts:** 1" in overview
    assert "**Legacy records (schema 1.x):** 2 records in 1 files (2026-06-14 to 2026-06-14)" in overview
    assert "**Sources:** 1 active, 0 probation, 1 dormant, 0 broken, 0 rejected, 0 retired" in overview
    assert "| example.com | US | 2 | 100.0% (2) | 2026-10-08 |" in overview
    assert "| `framing` | Widest view of the person among the images of this outfit. | 100.0% (4) | 1 |" in overview
    assert "| `garments` |" not in overview
    assert "| Outerwear | 2 | 50.0% (2) |" in overview


def test_overview_without_schema_2_records(tmp_path: Path) -> None:
    assert "**Schema 2.0 records:** none yet" in build_overview(tmp_path, NOW)


def test_readme_section_is_replaced(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(f"# Title\n\nIntro\n{START_MARKER}\nold overview\n{END_MARKER}\n\n## Next\n", encoding="utf-8")
    assert update_readme(readme, "new overview\n")
    text = readme.read_text(encoding="utf-8")
    assert f"{START_MARKER}\nnew overview\n{END_MARKER}" in text
    assert text.startswith("# Title\n\nIntro\n") and text.endswith("## Next\n")
    assert not update_readme(readme, "new overview\n")


def test_readme_without_markers_is_not_changed(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# Title\n", encoding="utf-8")
    assert not update_readme(readme, "overview\n")
    assert readme.read_text(encoding="utf-8") == "# Title\n"
