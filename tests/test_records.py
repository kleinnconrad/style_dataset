"""Tests for the record assembly in src/records.py."""
from pathlib import Path

from extraction import clean_extraction
from records import PostMetadata, SourceMetadata, build_records, make_record_id
from schema import SCHEMA_VERSION, ImageRef, PostExtraction

FIXTURE = Path(__file__).parent / "fixtures" / "post_extraction.json"

IMAGES = [ImageRef(hash=f"{n:016x}", width=1024, height=1536) for n in range(1, 5)]
POST = PostMetadata(
    url="https://www.example.com/2026/10/fall-outfit/?utm_source=rss",
    canonical_url="https://example.com/2026/10/fall-outfit/",
    title="Fall outfit",
    published_date="2026-10-08",
    tags=("Outfits",),
    shopping_link_count=3,
)
SOURCE = SourceMetadata(source_id="example.com", country="GB", country_basis="domain", language="en")


def build(second_age_group: str = "Young Adult") -> list:
    """Builds the records of the fixture response.

    Args:
        second_age_group: Age group set for the second outfit of the fixture.
    """
    raw = PostExtraction.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    raw.outfits[1].age_group = second_age_group
    cleaned, _ = clean_extraction(raw, image_count=4, reference_text="Coat by Everlane")
    return build_records(
        cleaned, IMAGES, POST, SOURCE, model="test-model", extraction_hash="abc", date_scraped="2026-10-10"
    )


def test_one_record_per_outfit_with_provenance() -> None:
    records = build()
    assert len(records) == 2
    first = records[0]
    assert first.schema_version == SCHEMA_VERSION
    assert first.source_region == "Europe"
    assert first.published_date == "2026-10-08"
    assert [image.hash for image in first.images] == [IMAGES[0].hash, IMAGES[1].hash]
    assert "image_indexes" not in first.model_dump()


def test_categories_and_outfit_colors_are_derived() -> None:
    first, second = build()
    assert [g.category for g in first.garments] == ["Outerwear", "Top", "Bottom", "Footwear", "Bag"]
    # The tote is an accessory, so its color is not an outfit color
    assert first.outfit_colors == ["Camel/Tan", "Beige/Cream", "Denim blue", "White"]
    assert second.outfit_colors == ["Black"]


def test_outfits_of_minors_are_not_stored() -> None:
    for age_group in ("Child", "Teen"):
        records = build(second_age_group=age_group)
        assert len(records) == 1
        assert records[0].age_group == "Adult"


def test_record_id_is_stable_and_ignores_url_variants() -> None:
    hashes = [IMAGES[0].hash, IMAGES[1].hash]
    record_id = make_record_id("https://example.com/2026/10/fall-outfit", hashes, 1)
    assert record_id == build()[0].record_id
    assert record_id == make_record_id("http://www.example.com/2026/10/fall-outfit/", list(reversed(hashes)), 1)
    assert record_id != make_record_id("https://example.com/2026/10/fall-outfit", hashes, 2)
