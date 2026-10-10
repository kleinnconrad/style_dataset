"""
Writes the dataset overview into the README.

The overview describes the schema 2.0 records field by field, summarizes the
legacy records (schema 1.x) and the source registry, and reports images that
appear in more than one post. It replaces the text between the markers
DATASET_OVERVIEW_START and DATASET_OVERVIEW_END.
"""
import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from schema import GARMENT_TYPES_BY_CATEGORY, SCHEMA_VERSION, OutfitRecord
from sources import SourceRegistry

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_FILE_GLOB = "*/*/fashion_analytics_*.json"
START_MARKER = "<!-- DATASET_OVERVIEW_START -->"
END_MARKER = "<!-- DATASET_OVERVIEW_END -->"
# Values that mean "no information" in the vocabularies
ABSENT_VALUES = frozenset({"Not visible", "Not applicable", "Unclear", "Unknown", "Unidentifiable"})
# Lists of objects, summarized in their own tables
NESTED_FIELDS = ("garments", "images")
STATUS_ORDER = ("active", "probation", "dormant", "broken", "rejected", "retired")
MAX_SOURCE_ROWS = 30
TOP_GARMENT_TYPES = 15


def load_records(data_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Reads all daily records files.

    Args:
        data_dir: Root folder of the dataset.

    Returns:
        tuple: Schema 2.x records, legacy records, and the dates of the files with legacy records.
    """
    current: list[dict[str, Any]] = []
    legacy: list[dict[str, Any]] = []
    legacy_dates: list[str] = []
    for path in sorted(data_dir.glob(DATA_FILE_GLOB)):
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.warning("Skipping unreadable file %s: %s", path, e)
            continue
        if not isinstance(records, list):
            continue
        file_has_legacy = False
        for record in records:
            if not isinstance(record, dict):
                continue
            if "schema_version" in record:
                current.append(record)
            else:
                legacy.append(record)
                file_has_legacy = True
        if file_has_legacy:
            legacy_dates.append(path.stem.rsplit("_", 1)[-1])
    return current, legacy, legacy_dates


def _is_known(value: Any) -> bool:
    """Returns True if a field value carries information."""
    if value is None:
        return False
    if isinstance(value, list):
        return any(_is_known(item) for item in value)
    if isinstance(value, str):
        return bool(value.strip()) and value not in ABSENT_VALUES
    return True


def _cell(text: str) -> str:
    """Escapes text for a markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def _percent(part: int, whole: int) -> str:
    """Formats a share as ``97.3% (1,234)``."""
    share = 100 * part / whole if whole else 0.0
    return f"{share:.1f}% ({part:,})"


def field_rows(records: list[dict[str, Any]]) -> list[str]:
    """Builds the field table of the schema 2.x records.

    Args:
        records: The schema 2.x records.

    Returns:
        list[str]: Markdown table lines.
    """
    lines = ["| Field | Description | Known values | Distinct values |", "|---|---|---|---|"]
    for name, field in OutfitRecord.model_fields.items():
        if name in NESTED_FIELDS:
            continue
        values = [record.get(name) for record in records]
        distinct: set[str] = set()
        for value in values:
            for item in (value if isinstance(value, list) else [value]):
                if _is_known(item):
                    distinct.add(str(item))
        known = sum(1 for value in values if _is_known(value))
        lines.append(f"| `{name}` | {_cell(field.description or '')} | {_percent(known, len(records))} | {len(distinct):,} |")
    return lines


def garment_rows(records: list[dict[str, Any]]) -> list[str]:
    """Builds the garment summary: items and outfits per category, and the most frequent types.

    Args:
        records: The schema 2.x records.

    Returns:
        list[str]: Markdown lines.
    """
    items: Counter = Counter()
    outfits_with: Counter = Counter()
    types: Counter = Counter()
    for record in records:
        garments = record.get("garments") or []
        for garment in garments:
            items[garment.get("category")] += 1
            types[garment.get("garment_type")] += 1
        for category in {garment.get("category") for garment in garments}:
            outfits_with[category] += 1
    lines = ["| Category | Items | Outfits with this category |", "|---|---|---|"]
    for category in GARMENT_TYPES_BY_CATEGORY:
        if items[category]:
            lines.append(f"| {category} | {items[category]:,} | {_percent(outfits_with[category], len(records))} |")
    top = ", ".join(f"{name} ({count:,})" for name, count in types.most_common(TOP_GARMENT_TYPES))
    lines.extend(["", f"Most frequent garment types: {top or 'none'}."])
    return lines


def source_rows(registry: SourceRegistry) -> list[str]:
    """Builds the registry summary and the table of active sources.

    Args:
        registry: The source registry.

    Returns:
        list[str]: Markdown lines.
    """
    counts = Counter(entry.status for entry in registry)
    summary = ", ".join(f"{counts[status]} {status}" for status in STATUS_ORDER)
    lines = [f"- **Sources:** {summary}" if len(registry) else "- **Sources:** none registered yet"]
    active = sorted(registry.with_status("active"), key=lambda e: (-e.posts_processed, e.source_id))
    if not active:
        return lines
    lines.extend(["", "**Active sources** (posts processed since admission)", "",
                  "| Source | Country | Posts | Posts with outfits | Last post |", "|---|---|---|---|---|"])
    for entry in active[:MAX_SOURCE_ROWS]:
        lines.append(f"| {entry.source_id} | {entry.country or 'unknown'} | {entry.posts_processed} | "
                     f"{_percent(entry.posts_with_outfits, entry.posts_processed)} | {entry.last_post_date or '-'} |")
    if len(active) > MAX_SOURCE_ROWS:
        lines.append(f"| ... {len(active) - MAX_SOURCE_ROWS} more | | | | |")
    return lines


def reused_images(records: list[dict[str, Any]]) -> int:
    """Counts images that appear in records of more than one post.

    One image can show two people of the same post; across posts the same
    image means a duplicate, which the pipeline is built to prevent.

    Args:
        records: The schema 2.x records.

    Returns:
        int: Number of such images.
    """
    posts_by_hash: dict[str, set[str]] = defaultdict(set)
    for record in records:
        for image in record.get("images") or []:
            posts_by_hash[image.get("hash")].add(record.get("post_url"))
    return sum(1 for posts in posts_by_hash.values() if len(posts) > 1)


def build_overview(data_dir: Path, now: datetime) -> str:
    """Builds the overview text.

    Args:
        data_dir: Root folder of the dataset, with ``state/`` inside.
        now: Time of the update.

    Returns:
        str: Markdown for the README section.
    """
    current, legacy, legacy_dates = load_records(data_dir)
    lines = [f"**Last updated:** {now:%Y-%m-%d %H:%M} UTC", ""]
    if current:
        posts = {record.get("post_url") for record in current}
        sources = {record.get("source_id") for record in current}
        published = sorted(record["published_date"] for record in current if record.get("published_date"))
        period = f", published {published[0]} to {published[-1]}" if published else ""
        lines.append(f"- **Schema {SCHEMA_VERSION} records:** {len(current):,} outfits from {len(posts):,} posts "
                     f"of {len(sources):,} sources{period}")
        lines.append(f"- **Images reused across posts:** {reused_images(current)}")
    else:
        lines.append(f"- **Schema {SCHEMA_VERSION} records:** none yet")
    if legacy:
        lines.append(f"- **Legacy records (schema 1.x):** {len(legacy):,} records in {len(legacy_dates)} files "
                     f"({min(legacy_dates)} to {max(legacy_dates)}); collected without deduplication and "
                     "without publish dates, and not included in the tables below")
    lines.extend(source_rows(SourceRegistry.load(data_dir / "state")))
    if current:
        lines.extend(["", f"**Fields (schema {SCHEMA_VERSION})**", ""])
        lines.extend(field_rows(current))
        lines.extend(["", "**Garments**", ""])
        lines.extend(garment_rows(current))
    return "\n".join(lines) + "\n"


def update_readme(readme_path: Path, overview: str) -> bool:
    """Replaces the overview section of the README.

    Args:
        readme_path: The README file.
        overview: The new section text.

    Returns:
        bool: True if the README was changed.
    """
    content = readme_path.read_text(encoding="utf-8")
    start = content.find(START_MARKER)
    end = content.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        logger.warning("%s has no overview markers; nothing was changed.", readme_path)
        return False
    updated = content[: start + len(START_MARKER)] + "\n" + overview + content[end:]
    if updated == content:
        return False
    readme_path.write_text(updated, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    """Updates the overview in the repository's README.

    Returns:
        int: Exit code.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    overview = build_overview(REPO_ROOT / "data", datetime.now(timezone.utc))
    if update_readme(REPO_ROOT / "README.md", overview):
        logger.info("README.md overview updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
