"""
Persistent state of the pipeline: processed posts and analyzed images.

The state lives in JSON files under ``<output>/state/``, which the workflow
commits together with the data. Each file has a version key and one entry per
line, so that git diffs stay readable, and is written atomically.
"""
import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

STATE_FILE_VERSION = 1
POSTS_FILE = "posts.json"
IMAGES_FILE = "images.json"
RUN_LOG_FILE = "run_log.jsonl"
# Failed attempts after which a post is given up. Quota and server errors do not count.
MAX_ATTEMPTS = 3
# Images whose 64-bit dHash differs in at most this many bits count as the same image
DHASH_MAX_DISTANCE = 5
MAX_ERROR_CHARS = 300

PostStatus = Literal["pending", "done", "no_images", "republished", "failed"]


def atomic_write_text(path: Path, text: str) -> None:
    """Writes a text file so that readers see either the old or the new content.

    Args:
        path: Target file; its folder is created if needed.
        text: Content, written as UTF-8 with LF line endings.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def append_run_log(state_dir: Path, entry: Mapping[str, Any]) -> None:
    """Appends one line with the summary of a run to ``run_log.jsonl``.

    Args:
        state_dir: Folder of the state files.
        entry: JSON-compatible summary of the run.
    """
    path = state_dir / RUN_LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def dump_entries(collection: str, entries: Mapping[str, Mapping[str, Any]], version: int = STATE_FILE_VERSION) -> str:
    """Serializes entries as JSON with sorted keys and one entry per line.

    Args:
        collection: Name of the object that holds the entries, e.g. ``posts``.
        entries: Entries by key.
        version: File format version.

    Returns:
        str: The JSON text.
    """
    lines = ["{", f'"version": {version},', f"{json.dumps(collection)}: {{"]
    body = [
        f"{json.dumps(key, ensure_ascii=False)}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}"
        for key, value in sorted(entries.items())
    ]
    if body:
        lines.append(",\n".join(body))
    lines.extend(["}", "}"])
    return "\n".join(lines) + "\n"


def load_entries(path: Path, collection: str, version: int = STATE_FILE_VERSION) -> dict[str, dict[str, Any]]:
    """Reads entries written by ``dump_entries``.

    Args:
        path: The JSON file; a missing file means no entries.
        collection: Name of the object that holds the entries.
        version: Expected file format version.

    Returns:
        dict: Entries by key.

    Raises:
        ValueError: If the file has another version or no such collection.
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != version:
        raise ValueError(f"{path} has version {data.get('version')!r}; expected {version}")
    entries = data.get(collection)
    if not isinstance(entries, dict):
        raise ValueError(f"{path} has no {collection!r} object")
    return entries


class PostEntry(BaseModel):
    """State of one post, keyed by its normalized URL.

    Keeping the title, date and tags allows a retry after the post has left the feed.
    """
    status: PostStatus = Field(description="pending until processed; done, no_images, republished or failed afterwards.")
    source_id: str
    url: str
    title: Optional[str] = None
    published: Optional[date] = None
    tags: list[str] = Field(default_factory=list)
    canonical_url: Optional[str] = None
    first_seen: date
    last_attempt: Optional[date] = None
    attempts: int = Field(default=0, description="Failed attempts that count toward MAX_ATTEMPTS.")
    outfits: int = Field(default=0, description="Number of stored outfit records.")
    last_error: Optional[str] = None


class ImageEntry(BaseModel):
    """An analyzed image, keyed by its dHash."""
    first_seen: date
    post_url: str


def _parse_hash(hash_hex: str) -> int:
    """Converts a dHash from 16 hexadecimal digits to an integer.

    Args:
        hash_hex: The hash as text.

    Returns:
        int: The 64-bit value.

    Raises:
        ValueError: If the text is not 16 hexadecimal digits.
    """
    if len(hash_hex) != 16:
        raise ValueError(f"dHash must have 16 hexadecimal digits: {hash_hex!r}")
    return int(hash_hex, 16)


class PipelineState:
    """Processed posts and analyzed images of all runs."""

    def __init__(self, state_dir: Path, posts: dict[str, PostEntry], images: dict[str, ImageEntry]) -> None:
        """Initializes the state.

        Args:
            state_dir: Folder of the state files.
            posts: Post entries by normalized URL.
            images: Image entries by dHash.
        """
        self._state_dir = state_dir
        self._posts = posts
        self._images = images
        self._image_values = [(_parse_hash(key), key) for key in images]

    @classmethod
    def load(cls, state_dir: Path) -> "PipelineState":
        """Reads the state files; missing files mean an empty state.

        Args:
            state_dir: Folder of the state files.

        Returns:
            PipelineState: The loaded state.
        """
        posts = {key: PostEntry.model_validate(value)
                 for key, value in load_entries(state_dir / POSTS_FILE, "posts").items()}
        images = {key: ImageEntry.model_validate(value)
                  for key, value in load_entries(state_dir / IMAGES_FILE, "images").items()}
        return cls(state_dir, posts, images)

    def save(self) -> None:
        """Writes both state files atomically."""
        atomic_write_text(
            self._state_dir / POSTS_FILE,
            dump_entries("posts", {key: entry.model_dump(mode="json") for key, entry in self._posts.items()}),
        )
        atomic_write_text(
            self._state_dir / IMAGES_FILE,
            dump_entries("images", {key: entry.model_dump(mode="json") for key, entry in self._images.items()}),
        )

    @property
    def posts(self) -> Mapping[str, PostEntry]:
        """Read-only view of the post entries by normalized URL."""
        return MappingProxyType(self._posts)

    def get(self, key: str) -> Optional[PostEntry]:
        """Returns the entry of a post, or None if the post is new.

        Args:
            key: Normalized URL of the post.
        """
        return self._posts.get(key)

    def is_due(self, key: str, today: date, lookback_days: int) -> bool:
        """Returns True if a post should be processed in this run.

        Args:
            key: Normalized URL of the post.
            today: Date of the run.
            lookback_days: Pending posts first seen longer ago are not retried.

        Returns:
            bool: True for new posts and for pending posts within the lookback window.
        """
        entry = self._posts.get(key)
        if entry is None:
            return True
        return entry.status == "pending" and entry.first_seen >= today - timedelta(days=lookback_days)

    def pending_for_source(self, source_id: str, today: date, lookback_days: int) -> list[tuple[str, PostEntry]]:
        """Returns the pending posts of a source that are due for a retry.

        Args:
            source_id: The source.
            today: Date of the run.
            lookback_days: Pending posts first seen longer ago are not returned.

        Returns:
            list: ``(key, entry)`` pairs.
        """
        return [
            (key, entry) for key, entry in self._posts.items()
            if entry.source_id == source_id and self.is_due(key, today, lookback_days)
        ]

    def register(
        self,
        key: str,
        *,
        source_id: str,
        url: str,
        title: Optional[str],
        published: Optional[date],
        tags: Iterable[str],
        today: date,
    ) -> PostEntry:
        """Creates a pending entry for a new post; existing entries are returned unchanged.

        Args:
            key: Normalized URL of the post.
            source_id: The source of the post.
            url: URL of the post as given by the feed.
            title: Title of the post.
            published: Publish date of the post.
            tags: Categories and tags of the post.
            today: Date of the run.

        Returns:
            PostEntry: The entry of the post.
        """
        entry = self._posts.get(key)
        if entry is None:
            entry = PostEntry(
                status="pending", source_id=source_id, url=url, title=title,
                published=published, tags=list(tags), first_seen=today,
            )
            self._posts[key] = entry
        return entry

    def complete(
        self,
        key: str,
        status: PostStatus,
        *,
        today: date,
        outfits: int = 0,
        image_hashes: Iterable[str] = (),
        canonical_url: Optional[str] = None,
    ) -> PostEntry:
        """Marks a registered post as processed and records its images.

        Image hashes are recorded only here, after a successful extraction, so
        that a retried post does not find its own images marked as seen.

        Args:
            key: Normalized URL of the post.
            status: done, no_images or republished.
            today: Date of the run.
            outfits: Number of stored outfit records.
            image_hashes: dHashes of the images sent to the model.
            canonical_url: Canonical URL declared by the post page.

        Returns:
            PostEntry: The updated entry.

        Raises:
            KeyError: If the post was not registered.
            ValueError: For statuses that do not end processing.
        """
        if status not in ("done", "no_images", "republished"):
            raise ValueError(f"complete() does not accept status {status!r}")
        entry = self._posts[key].model_copy(update={
            "status": status, "last_attempt": today, "outfits": outfits,
            "canonical_url": canonical_url, "last_error": None,
        })
        self._posts[key] = entry
        self.add_images(image_hashes, entry.url, today)
        return entry

    def fail(self, key: str, error: str, *, counts_as_attempt: bool, today: date) -> PostEntry:
        """Records a failed attempt. After MAX_ATTEMPTS counted failures the post is given up.

        Args:
            key: Normalized URL of the post.
            error: Description of the failure.
            counts_as_attempt: False for quota and server errors, which say nothing about the post.
            today: Date of the run.

        Returns:
            PostEntry: The updated entry.

        Raises:
            KeyError: If the post was not registered.
        """
        entry = self._posts[key]
        attempts = entry.attempts + (1 if counts_as_attempt else 0)
        entry = entry.model_copy(update={
            "attempts": attempts,
            "status": "failed" if attempts >= MAX_ATTEMPTS else "pending",
            "last_attempt": today,
            "last_error": error[:MAX_ERROR_CHARS],
        })
        self._posts[key] = entry
        return entry

    def expire_pending(self, today: date, lookback_days: int) -> int:
        """Gives up pending posts that were first seen before the lookback window.

        Args:
            today: Date of the run.
            lookback_days: Length of the lookback window in days.

        Returns:
            int: Number of posts marked as failed.
        """
        cutoff = today - timedelta(days=lookback_days)
        expired = 0
        for key, entry in self._posts.items():
            if entry.status == "pending" and entry.first_seen < cutoff:
                self._posts[key] = entry.model_copy(update={
                    "status": "failed", "last_error": f"not completed within {lookback_days} days",
                })
                expired += 1
        return expired

    def find_similar_image(self, hash_hex: str) -> Optional[str]:
        """Returns the hash of an analyzed image that is the same as or close to the given one.

        Args:
            hash_hex: dHash of a new image.

        Returns:
            Optional[str]: The matching hash, or None if the image is new.
        """
        if hash_hex in self._images:
            return hash_hex
        value = _parse_hash(hash_hex)
        for known_value, known_hex in self._image_values:
            if (value ^ known_value).bit_count() <= DHASH_MAX_DISTANCE:
                return known_hex
        return None

    def add_images(self, image_hashes: Iterable[str], post_url: str, today: date) -> None:
        """Records analyzed images.

        Args:
            image_hashes: dHashes of the images.
            post_url: URL of the post that contained them.
            today: Date of the run.
        """
        for hash_hex in image_hashes:
            if hash_hex not in self._images:
                self._image_values.append((_parse_hash(hash_hex), hash_hex))
                self._images[hash_hex] = ImageEntry(first_seen=today, post_url=post_url)
