"""Tests for the schema 2.0 models in src/schema.py."""
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from google.genai import _transformers
from pydantic import ValidationError

from schema import (
    GARMENT_CATEGORY_BY_TYPE,
    GARMENT_TYPES_BY_CATEGORY,
    SCHEMA_VERSION,
    PostExtraction,
    describe_models,
    region_for_country,
)

FIXTURE = Path(__file__).parent / "fixtures" / "post_extraction.json"

# sha256 of describe_models(include_descriptions=False) per schema version. A change
# of field names, types or required flags must come with a new SCHEMA_VERSION.
SCHEMA_SNAPSHOTS = {
    "2.0": "203566060a94c0d44a71d53837d4013e961b5f2f072baa998e1413cc4cd0855a",
}


def load_fixture() -> dict[str, Any]:
    """Returns a fresh copy of the fixture response."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_validates() -> None:
    extraction = PostExtraction.model_validate(load_fixture())
    assert len(extraction.outfits) == 2
    assert extraction.outfits[0].garments[0].garment_type == "Coat"


def test_layering_complexity_is_clamped() -> None:
    extraction = PostExtraction.model_validate(load_fixture())
    assert extraction.outfits[1].layering_complexity == 5


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("outfits", 0, "weather"), "Drizzle"),
        (("outfits", 0, "garments", 0, "garment_type"), "Poncho jacket"),
        (("rejected_images", 0, "reason"), "Blurry"),
    ],
)
def test_values_outside_the_vocabulary_are_rejected(path: tuple, value: str) -> None:
    data = load_fixture()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        PostExtraction.model_validate(data)


def test_fields_are_required() -> None:
    data = load_fixture()
    del data["outfits"][0]["weather"]
    with pytest.raises(ValidationError):
        PostExtraction.model_validate(data)


def test_garment_types_are_unique_across_categories() -> None:
    type_count = sum(len(types) for types in GARMENT_TYPES_BY_CATEGORY.values())
    assert len(GARMENT_CATEGORY_BY_TYPE) == type_count


def _collect_properties(schema: Any, path: str = "") -> list[tuple[str, Any]]:
    """Returns all object properties of a converted schema with their paths."""
    found = []
    for name, prop in (schema.properties or {}).items():
        found.append((f"{path}.{name}", prop))
        found.extend(_collect_properties(prop, f"{path}.{name}"))
    if schema.items is not None:
        found.extend(_collect_properties(schema.items, f"{path}[]"))
    return found


def test_sdk_conversion_keeps_descriptions_and_enums() -> None:
    # Private SDK function: this test catches conversion changes after dependency updates
    converted = _transformers.t_schema(None, PostExtraction)
    properties = _collect_properties(converted)
    missing = [path for path, prop in properties if not prop.description]
    assert not missing, f"Properties without description: {missing}"
    garment_type = dict(properties)[".outfits[].garments[].garment_type"]
    assert len(garment_type.enum) == len(GARMENT_CATEGORY_BY_TYPE)
    assert converted.property_ordering[0] == "visual_analysis"


def test_schema_snapshot_matches_version() -> None:
    current = hashlib.sha256(describe_models(include_descriptions=False).encode("utf-8")).hexdigest()
    assert SCHEMA_VERSION in SCHEMA_SNAPSHOTS, f"Add a snapshot for schema version {SCHEMA_VERSION}: {current}"
    assert SCHEMA_SNAPSHOTS[SCHEMA_VERSION] == current, (
        "The schema 2.x models changed. Bump SCHEMA_VERSION and add the new snapshot: " + current
    )


def test_snapshot_text_excludes_descriptions() -> None:
    assert "description=" in describe_models(include_descriptions=True)
    assert "description=" not in describe_models(include_descriptions=False)


@pytest.mark.parametrize(
    ("country", "region"),
    [("US", "North America"), ("gb", "Europe"), ("DE", "Europe"), ("BR", "South America"),
     ("ZZ", "Other"), (None, "Unknown"), ("", "Unknown")],
)
def test_region_for_country(country: Any, region: str) -> None:
    assert region_for_country(country) == region
