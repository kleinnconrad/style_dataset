"""
Automatically managed registry of the blogs the pipeline reads.

Fixed rules admit, pause and retire sources; nothing is curated by hand. The
registry is seeded once from the domains of the legacy records and is stored
in ``<output>/state/sources.json``.
"""
import json
import logging
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from feeds import BLOCKED_STATUSES, Fetcher, ParsedFeed, count_recent_posts, find_feed, http_get
from schema import CountryBasis
from state import atomic_write_text, dump_entries, load_entries
from urls import domain_id

logger = logging.getLogger(__name__)

REGISTRY_FILE = "sources.json"

# Lifecycle rules
ACTIVITY_WINDOW_DAYS = 60       # admission and reactivation need MIN_RECENT_POSTS posts within this window
MIN_RECENT_POSTS = 2
DORMANT_AFTER_DAYS = 90         # a source without a post for this long becomes dormant
PROBATION_POSTS = 4             # a source on probation is decided after at most this many processed posts
PROBATION_MIN_SUCCESSES = 2     # of those, this many must produce an outfit
RECENT_WINDOW = 12              # processed posts considered for retirement
RETIRE_BELOW_SHARE = 0.25       # an active source is retired below this share of posts with an outfit
BROKEN_AFTER_FAILURES = 7       # consecutive daily runs with a failing feed
LEGACY_ACTIVE_MIN_RECORDS = 5   # legacy sources with this many records skip probation
MAX_ADMITTED_SOURCES = 60       # active and probation sources together
SEED_WORKERS = 8

SourceStatus = Literal["probation", "active", "dormant", "broken", "rejected", "retired"]
SourceOrigin = Literal["legacy", "discovery"]
ADMITTED_STATUSES = frozenset({"probation", "active"})
TERMINAL_STATUSES = frozenset({"rejected", "retired"})

# Country-code top-level domains that are mostly used for other purposes than the country
GENERIC_CCTLDS = frozenset({"ac", "ai", "cc", "co", "eu", "fm", "gg", "io", "la", "ly", "me", "sh", "so", "su",
                            "to", "tv", "ws"})
CCTLD_EXCEPTIONS = {"uk": "GB"}


class SourceEntry(BaseModel):
    """A blog in the registry, keyed by its domain."""
    source_id: str = Field(description="Domain of the blog, without www.")
    homepage: str
    feed_url: Optional[str] = None
    status: SourceStatus
    status_since: date
    previous_status: Optional[SourceStatus] = Field(
        default=None, description="Status to return to when a dormant or broken source works again.")
    origin: SourceOrigin
    country: Optional[str] = None
    country_basis: CountryBasis = "unknown"
    language: Optional[str] = None
    legacy_records: int = 0
    added: date
    posts_processed: int = 0
    posts_with_outfits: int = 0
    recent_outcomes: list[bool] = Field(
        default_factory=list, description="Whether each of the last processed posts produced an outfit.")
    last_post_date: Optional[date] = None
    posts_last_60_days: int = 0
    consecutive_feed_failures: int = 0
    last_feed_check: Optional[date] = None
    matched_selector: Optional[str] = None
    note: Optional[str] = Field(default=None, description="Reason for the current status.")


def language_code(feed_language: Optional[str]) -> Optional[str]:
    """Returns the ISO 639-1 language of an RSS language tag.

    Args:
        feed_language: Tag such as ``en-GB`` or ``de``.

    Returns:
        Optional[str]: For example ``en``, or None.
    """
    if not feed_language:
        return None
    primary = feed_language.replace("_", "-").split(",")[0].strip().split("-")[0].lower()
    return primary if primary.isalpha() and 2 <= len(primary) <= 3 else None


def infer_country(source_id: str, feed_language: Optional[str]) -> tuple[Optional[str], CountryBasis]:
    """Infers the country of a blog without a model call.

    The country-code top-level domain comes first. The region of the RSS
    language tag comes second, because WordPress sets ``en-US`` by default.
    Gemini later replaces both (see discovery.py).

    Args:
        source_id: Domain of the blog.
        feed_language: Language tag of the feed.

    Returns:
        tuple: ISO 3166-1 alpha-2 country or None, and the basis.
    """
    tld = source_id.rsplit(".", 1)[-1].lower()
    if len(tld) == 2 and tld.isalpha() and tld not in GENERIC_CCTLDS:
        return CCTLD_EXCEPTIONS.get(tld, tld.upper()), "domain"
    if feed_language:
        parts = feed_language.replace("_", "-").split(",")[0].strip().split("-")
        if len(parts) > 1 and len(parts[1]) == 2 and parts[1].isalpha():
            return parts[1].upper(), "feed_language"
    return None, "unknown"


def _transition(source: SourceEntry, status: SourceStatus, today: date, reason: str,
                remember: bool = False) -> SourceEntry:
    """Returns a copy of a source with a new status.

    Args:
        source: The source.
        status: The new status.
        today: Date of the change.
        reason: Why the status changes; stored in ``note``.
        remember: Keep the current status in ``previous_status`` to return to it later.

    Returns:
        SourceEntry: The updated copy.
    """
    return source.model_copy(update={
        "status": status,
        "status_since": today,
        "previous_status": source.status if remember else None,
        "note": reason,
    })


def apply_rules(source: SourceEntry, today: date) -> SourceEntry:
    """Applies the lifecycle rules to one source.

    Args:
        source: The source with its current statistics.
        today: Date of the run.

    Returns:
        SourceEntry: The source, with a new status if a rule applies.
    """
    if source.status in TERMINAL_STATUSES:
        return source
    if source.status == "broken":
        if source.consecutive_feed_failures == 0:
            return _transition(source, source.previous_status or "probation", today, "feed works again")
        return source
    if source.consecutive_feed_failures >= BROKEN_AFTER_FAILURES:
        return _transition(source, "broken", today,
                           f"feed failed on {source.consecutive_feed_failures} consecutive runs", remember=True)
    if source.status == "dormant":
        if source.posts_last_60_days >= MIN_RECENT_POSTS:
            return _transition(source, source.previous_status or "probation", today, "publishes again")
        return source
    if source.last_post_date is not None and (today - source.last_post_date).days > DORMANT_AFTER_DAYS:
        return _transition(source, "dormant", today, f"no post for more than {DORMANT_AFTER_DAYS} days",
                           remember=True)
    successes = sum(source.recent_outcomes)
    if source.status == "probation":
        failures = len(source.recent_outcomes) - successes
        if successes >= PROBATION_MIN_SUCCESSES:
            return _transition(source, "active", today, f"{successes} posts with outfits on probation")
        if failures > PROBATION_POSTS - PROBATION_MIN_SUCCESSES:
            return _transition(source, "rejected", today, f"{failures} posts without outfits on probation")
        return source
    window = source.recent_outcomes[-RECENT_WINDOW:]
    if len(window) >= RECENT_WINDOW and sum(window) < RETIRE_BELOW_SHARE * RECENT_WINDOW:
        return _transition(source, "retired", today,
                           f"only {sum(window)} of the last {RECENT_WINDOW} posts produced an outfit")
    return source


def record_post_outcome(source: SourceEntry, produced_outfit: bool) -> SourceEntry:
    """Adds the outcome of a processed post to the statistics of its source.

    Args:
        source: The source.
        produced_outfit: Whether at least one outfit record was stored.

    Returns:
        SourceEntry: The updated copy.
    """
    return source.model_copy(update={
        "posts_processed": source.posts_processed + 1,
        "posts_with_outfits": source.posts_with_outfits + int(produced_outfit),
        "recent_outcomes": (source.recent_outcomes + [produced_outfit])[-RECENT_WINDOW:],
    })


def record_feed_result(source: SourceEntry, feed: Optional[ParsedFeed], today: date) -> SourceEntry:
    """Updates the feed statistics of a source after a fetch.

    Args:
        source: The source.
        feed: The parsed feed, or None if fetching failed.
        today: Date of the run.

    Returns:
        SourceEntry: The updated copy.
    """
    if feed is None:
        return source.model_copy(update={
            "consecutive_feed_failures": source.consecutive_feed_failures + 1,
            "last_feed_check": today,
        })
    update = {
        "consecutive_feed_failures": 0,
        "last_feed_check": today,
        "feed_url": feed.url,
        "posts_last_60_days": count_recent_posts(feed, today, ACTIVITY_WINDOW_DAYS),
        "last_post_date": max(filter(None, (source.last_post_date, feed.newest_post)), default=None),
        "language": source.language or language_code(feed.language),
    }
    if source.country_basis == "unknown":
        update["country"], update["country_basis"] = infer_country(source.source_id, feed.language)
    return source.model_copy(update=update)


def evaluate_candidate(
    homepage: str,
    origin: SourceOrigin,
    today: date,
    fetch: Fetcher = http_get,
    legacy_records: int = 0,
) -> SourceEntry:
    """Checks a blog and builds its registry entry.

    Args:
        homepage: URL of the blog's homepage.
        origin: Where the candidate comes from.
        today: Date of the check.
        fetch: Fetcher function.
        legacy_records: Number of legacy records of the domain.

    Returns:
        SourceEntry: ``rejected`` if the site blocks requests or has no feed;
        ``broken`` if the site is not reachable, so that it is checked again;
        ``dormant`` with fewer than MIN_RECENT_POSTS recent posts; otherwise
        ``active`` for productive legacy sources and ``probation`` for all others.
    """
    source_id = domain_id(homepage)
    intended: SourceStatus = (
        "active" if origin == "legacy" and legacy_records >= LEGACY_ACTIVE_MIN_RECORDS else "probation"
    )
    base = SourceEntry(
        source_id=source_id, homepage=homepage, status=intended, status_since=today, origin=origin,
        legacy_records=legacy_records, added=today, last_feed_check=today,
    )
    search = find_feed(homepage, fetch)
    if search.feed is None:
        status = search.homepage_status
        if status is None or status == 429 or status >= 500:
            reason = "site not reachable" if status is None else f"site not reachable (HTTP {status})"
            failed = base.model_copy(update={"consecutive_feed_failures": 1})
            return _transition(failed, "broken", today, reason, remember=True)
        if status in BLOCKED_STATUSES:
            reason = f"site blocks automated requests (HTTP {status})"
        elif status == 200:
            reason = "no feed found"
        else:
            reason = f"site not found (HTTP {status})"
        return _transition(base, "rejected", today, reason)
    entry = record_feed_result(base.model_copy(update={"note": f"admitted from {origin}"}), search.feed, today)
    if entry.posts_last_60_days < MIN_RECENT_POSTS:
        entry = _transition(entry, "dormant", today,
                            f"fewer than {MIN_RECENT_POSTS} posts in {ACTIVITY_WINDOW_DAYS} days", remember=True)
    return entry


class SourceRegistry:
    """All known sources, by domain."""

    def __init__(self, path: Path, entries: dict[str, SourceEntry]) -> None:
        """Initializes the registry.

        Args:
            path: The registry file.
            entries: Sources by domain.
        """
        self._path = path
        self._entries = entries

    @classmethod
    def load(cls, state_dir: Path) -> "SourceRegistry":
        """Reads the registry; a missing file means an empty registry.

        Args:
            state_dir: Folder of the state files.

        Returns:
            SourceRegistry: The registry.
        """
        path = state_dir / REGISTRY_FILE
        entries = {key: SourceEntry.model_validate(value) for key, value in load_entries(path, "sources").items()}
        return cls(path, entries)

    def save(self) -> None:
        """Writes the registry atomically."""
        atomic_write_text(
            self._path,
            dump_entries("sources", {key: entry.model_dump(mode="json") for key, entry in self._entries.items()}),
        )

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, source_id: object) -> bool:
        return source_id in self._entries

    def __iter__(self) -> Iterator[SourceEntry]:
        return iter(list(self._entries.values()))

    def get(self, source_id: str) -> Optional[SourceEntry]:
        """Returns a source, or None if the domain is unknown."""
        return self._entries.get(source_id)

    def put(self, entry: SourceEntry) -> None:
        """Adds a source or replaces its entry."""
        self._entries[entry.source_id] = entry

    def with_status(self, *statuses: str) -> list[SourceEntry]:
        """Returns the sources with one of the given statuses, sorted by domain."""
        return sorted((e for e in self._entries.values() if e.status in statuses), key=lambda e: e.source_id)

    def admitted_count(self) -> int:
        """Number of active and probation sources."""
        return sum(1 for entry in self._entries.values() if entry.status in ADMITTED_STATUSES)

    def admit(self, entry: SourceEntry) -> bool:
        """Adds a newly evaluated source, respecting MAX_ADMITTED_SOURCES.

        Args:
            entry: The evaluated source.

        Returns:
            bool: False if the source would be admitted but the registry is full;
            it is then not stored, so that it can be proposed again later.
        """
        if entry.status in ADMITTED_STATUSES and self.admitted_count() >= MAX_ADMITTED_SOURCES:
            return False
        self.put(entry)
        return True

    def apply_rules(self, today: date) -> list[tuple[str, str, str]]:
        """Applies the lifecycle rules to all sources.

        Args:
            today: Date of the run.

        Returns:
            list: ``(source_id, old status, new status)`` for every change.
        """
        changes = []
        for source_id, entry in list(self._entries.items()):
            updated = apply_rules(entry, today)
            if updated.status != entry.status:
                changes.append((source_id, entry.status, updated.status))
                logger.info("Source %s: %s -> %s (%s).", source_id, entry.status, updated.status, updated.note)
            self._entries[source_id] = updated
        return changes


@dataclass(frozen=True)
class LegacyDomain:
    """A domain that appears in the legacy records.

    Attributes:
        homepage (str): Homepage URL, built from the most frequent scheme and host.
        records (int): Number of legacy records.
    """
    homepage: str
    records: int


def collect_legacy_domains(data_dir: Path) -> dict[str, LegacyDomain]:
    """Counts the legacy records per domain.

    Args:
        data_dir: Folder with ``YYYY/MM/fashion_analytics_*.json`` files.

    Returns:
        dict: Legacy domains by domain id.
    """
    roots: dict[str, Counter] = {}
    for path in sorted(data_dir.glob("*/*/fashion_analytics_*.json")):
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.warning("Skipping unreadable legacy file %s: %s", path, e)
            continue
        for record in records if isinstance(records, list) else []:
            url = record.get("source_url") if isinstance(record, dict) else None
            if not url:
                continue
            parts = urlsplit(url)
            try:
                key = domain_id(url)
            except ValueError:
                continue
            roots.setdefault(key, Counter())[f"{parts.scheme}://{parts.netloc}/"] += 1
    return {key: LegacyDomain(homepage=counter.most_common(1)[0][0], records=sum(counter.values()))
            for key, counter in roots.items()}


def seed_from_legacy(
    registry: SourceRegistry,
    data_dir: Path,
    today: date,
    fetch: Fetcher = http_get,
    workers: int = SEED_WORKERS,
) -> Counter:
    """Fills an empty registry from the domains of the legacy records.

    Domains are admitted in the order of their legacy record count, so that the
    most productive ones are admitted first when MAX_ADMITTED_SOURCES is reached.

    Args:
        registry: The registry to fill.
        data_dir: Folder with the legacy records.
        today: Date of the run.
        fetch: Fetcher function.
        workers: Number of domains checked in parallel.

    Returns:
        Counter: Number of domains per resulting status, plus ``not admitted (full)``.
    """
    domains = collect_legacy_domains(data_dir)
    logger.info("Seeding the source registry from %d legacy domains.", len(domains))
    ordered = sorted(domains.items(), key=lambda item: (-item[1].records, item[0]))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        entries = list(pool.map(
            lambda item: evaluate_candidate(item[1].homepage, "legacy", today, fetch, item[1].records), ordered))
    summary: Counter = Counter()
    for entry in entries:
        if registry.admit(entry):
            summary[entry.status] += 1
        else:
            summary["not admitted (full)"] += 1
    logger.info("Seeding result: %s", dict(summary))
    return summary
