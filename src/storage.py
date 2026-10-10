"""
Writes outfit records to the daily files of the dataset.

Records are merged into the file of their UTC day. A record with a known
``record_id`` is replaced, and the records of a reprocessed post are replaced
as a group, so that a run that was interrupted between writing records and
writing state does not create duplicates. Records without ``record_id``
(schema 1.x) are kept unchanged.
"""
import json
import logging
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, Optional

from schema import OutfitRecord
from state import atomic_write_text
from urls import normalize_url

logger = logging.getLogger(__name__)


def day_file(output_dir: Path, day: date) -> Path:
    """Returns the path of the records file of a day.

    Args:
        output_dir: Root folder of the dataset.
        day: The UTC day.

    Returns:
        Path: ``<output_dir>/YYYY/MM/fashion_analytics_YYYY-MM-DD.json``.
    """
    return output_dir / f"{day:%Y}" / f"{day:%m}" / f"fashion_analytics_{day.isoformat()}.json"


def load_day(path: Path) -> list[dict[str, Any]]:
    """Reads a records file.

    Args:
        path: The file; a missing file means no records.

    Returns:
        list[dict]: The records.

    Raises:
        ValueError: If the file does not contain a JSON list.
    """
    if not path.exists():
        return []
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"{path} does not contain a list of records")
    return records


def _post_key(record: dict[str, Any]) -> Optional[str]:
    """Returns the normalized post URL of a schema 2.x record, or None for other records."""
    url = record.get("post_url")
    if record.get("record_id") is None or not url:
        return None
    return normalize_url(url)


def append_records(
    output_dir: Path, day: date, records: Sequence[OutfitRecord], post_key: Optional[str] = None
) -> Optional[Path]:
    """Merges records into the file of a day.

    Args:
        output_dir: Root folder of the dataset.
        day: The UTC day.
        records: New records.
        post_key: Normalized URL of the post the records come from; earlier
            records of the same post in this file are replaced.

    Returns:
        Optional[Path]: The written file, or None if nothing changed.
    """
    path = day_file(output_dir, day)
    existing = load_day(path)
    new = [record.model_dump(mode="json") for record in records]
    new_ids = {record["record_id"] for record in new}
    kept = [
        record for record in existing
        if record.get("record_id") not in new_ids and (post_key is None or _post_key(record) != post_key)
    ]
    if not new and len(kept) == len(existing):
        return None
    atomic_write_text(path, json.dumps(kept + new, indent=4, ensure_ascii=False) + "\n")
    replaced = len(existing) - len(kept)
    logger.info("Stored %d records in %s%s.", len(new), path, f" ({replaced} replaced)" if replaced else "")
    return path
