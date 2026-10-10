"""
Weekly maintenance of the source registry.

The weekly run
1. re-checks the feeds of dormant and broken sources, so that they can return,
2. asks Gemini with Google Search for the country and language of sources
   whose country was so far inferred only from the domain or the feed,
3. asks Gemini with Google Search for new blogs, with a search focus that
   changes every week, and admits the candidates that pass the admission rules.

Usage:
    uv run python src/discovery.py [--output-dir PATH]
"""
import argparse
import json
import logging
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, TypeVar
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv
from google.genai import errors, types
from pydantic import AliasChoices, BaseModel, Field, ValidationError

from content import SHOP_DOMAINS, SOCIAL_DOMAINS
from extraction import (
    MIN_CALL_INTERVAL_S,
    CallBudgetExceededError,
    FatalRequestError,
    QuotaExhaustedError,
    RunStopError,
    TransientExtractionError,
    create_client,
)
from feeds import FeedError, Fetcher, ParsedFeed, fetch_feed, find_feed, http_get
from pipeline import LEGACY_DATA_DIR
from settings import Settings
from sources import (
    ADMITTED_STATUSES,
    MAX_ADMITTED_SOURCES,
    SourceEntry,
    SourceRegistry,
    evaluate_candidate,
    language_code,
    record_feed_result,
    seed_from_legacy,
)
from state import append_run_log
from urls import domain_id

logger = logging.getLogger(__name__)

MAX_DISCOVERY_CALLS = 12
LOOKUP_BATCH_SIZE = 20
LOOKUP_REPEAT_DAYS = 180
CANDIDATES_PER_RUN = 15
MAX_ADMISSIONS_PER_WEEK = 10
RECHECK_WORKERS = 8
COUNTRY_LOOKUP_TEMPERATURE = 0.0
DISCOVERY_TEMPERATURE = 0.7
# The focus changes every ISO week, to widen the sample beyond the US-centric legacy sources
SEARCH_FOCUS = (
    "menswear and men's personal style",
    "personal style blogs from France, Germany, Italy, Spain and Scandinavia",
    "plus-size fashion",
    "style over 50",
    "students and young adults",
    "petite fashion",
    "secondhand, vintage and sustainable fashion",
    "workwear and office outfits",
    "fashion bloggers in Asia",
    "fashion bloggers in the United Kingdom and Ireland",
    "fashion bloggers in Australia and New Zealand",
    "modest fashion",
)
MAX_CANDIDATES_EVALUATED = 30
# Hosts that are never personal blogs: platforms, magazines, newspapers and Gemini's citation redirects
EXCLUDED_CANDIDATE_DOMAINS = SOCIAL_DOMAINS | SHOP_DOMAINS | frozenset({
    "vertexaisearch.cloud.google.com", "google.com", "wikipedia.org", "reddit.com", "quora.com", "medium.com",
    "feedspot.com", "vogue.com", "vogue.co.uk", "elle.com", "harpersbazaar.com", "whowhatwear.com",
    "refinery29.com", "instyle.com", "glamour.com", "cosmopolitan.com", "marieclaire.com", "stylecaster.com",
    "thecut.com", "popsugar.com", "byrdie.com", "buzzfeed.com", "nytimes.com", "theguardian.com",
    "businessinsider.com", "forbes.com", "who.com", "people.com",
})
DOMAIN_PATTERN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")

COUNTRY_PROMPT = """\
For each of the following fashion blogs, find out in which country the author lives and in which language the blog is written. Use the blog's about page and other sources found with Google Search.

Blogs:
{domains}

Return only a JSON array without other text, with one object per blog:
[{{"domain": "example.com", "country": "ISO 3166-1 alpha-2 code, or null if unknown", "language": "ISO 639-1 code, or null if unknown"}}]"""

DISCOVERY_PROMPT = """\
Use Google Search to find personal style blogs that published outfit posts with photos of the author's own outfits in {current_month} or {previous_month}. Search for example for "outfit of the day", "what I wore" or "outfit post" together with the focus.
Focus: {focus}.

Requirements:
- The blog published at least two outfit posts in the last two months.
- It is a personal blog, not a magazine, online shop, brand or social media profile.
- It has its own domain or is hosted on Blogger, WordPress.com or Substack.

Do not suggest these domains: {excluded}.

Return only a JSON array with up to {count} objects, without other text. For each blog, give the URL of a recent outfit post that you found in the search results:
[{{"post_url": "https://example.com/2026/10/fall-outfit/", "country": "ISO 3166-1 alpha-2 code of the author's country, or null", "language": "ISO 639-1 code of the blog's language, or null"}}]"""
# Answers that are common but not ISO 3166-1 alpha-2 codes
COUNTRY_ALIASES = {"UK": "GB", "EL": "GR"}


@dataclass(frozen=True)
class SearchAnswer:
    """Answer of a request with Google Search.

    Attributes:
        text (str): The answer text; empty if the model returned no text.
        source_domains (tuple[str, ...]): Domains of the web pages that Google Search
            returned for the answer, from the grounding metadata.
    """
    text: str
    source_domains: tuple[str, ...] = ()


def _source_domains(response: Any) -> tuple[str, ...]:
    """Reads the domains of the search sources from a response's grounding metadata.

    The Gemini API gives the domain of a source as its title; titles that are
    not domains are ignored.

    Args:
        response: The ``GenerateContentResponse``.

    Returns:
        tuple[str, ...]: Domains without duplicates, in answer order.
    """
    domains: dict[str, None] = {}
    for candidate in getattr(response, "candidates", None) or []:
        metadata = getattr(candidate, "grounding_metadata", None)
        for chunk in getattr(metadata, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            for value in (getattr(web, "domain", None), getattr(web, "title", None)):
                name = (value or "").strip().lower().removeprefix("www.")
                if DOMAIN_PATTERN.match(name):
                    domains.setdefault(name, None)
                    break
    return tuple(domains)


class SearchModel:
    """Gemini with Google Search grounding, for short JSON answers.

    Attributes:
        calls (int): Requests sent in this run.
    """

    def __init__(self, settings: Settings, client: Optional[Any] = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        """Initializes the model.

        Args:
            settings: The run settings.
            client: A ``genai.Client`` or a compatible test double; created from GEMINI_API_KEY if omitted.
            sleep: Function used to wait between calls; replaceable in tests.
        """
        self._client = client if client is not None else create_client()
        self._model = settings.gemini_model
        self._sleep = sleep
        self.calls = 0

    def ask(self, prompt: str, temperature: float) -> SearchAnswer:
        """Sends a prompt with Google Search enabled.

        Args:
            prompt: The prompt.
            temperature: Sampling temperature.

        Returns:
            SearchAnswer: The answer text and the domains of its search sources.

        Raises:
            CallBudgetExceededError: After MAX_DISCOVERY_CALLS calls.
            QuotaExhaustedError: For 429 responses.
            FatalRequestError: For other client errors.
            TransientExtractionError: For server errors and network errors after the SDK retries.
        """
        if self.calls >= MAX_DISCOVERY_CALLS:
            raise CallBudgetExceededError(f"MAX_DISCOVERY_CALLS ({MAX_DISCOVERY_CALLS}) reached")
        if self.calls:
            self._sleep(MIN_CALL_INTERVAL_S)
        self.calls += 1
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=temperature,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        try:
            response = self._client.models.generate_content(model=self._model, contents=prompt, config=config)
        except errors.ClientError as e:
            if e.code == 429:
                raise QuotaExhaustedError(f"Gemini quota exhausted: {e.message}") from e
            raise FatalRequestError(f"Gemini rejected the request: {e}") from e
        except errors.ServerError as e:
            raise TransientExtractionError(f"Gemini server error after retries: {e}") from e
        except httpx.TransportError as e:
            raise TransientExtractionError(f"Network error after retries: {e!r}") from e
        return SearchAnswer(text=getattr(response, "text", None) or "", source_domains=_source_domains(response))


class CountryAnswer(BaseModel):
    """One entry of the country lookup answer."""
    domain: str
    country: Optional[str] = None
    language: Optional[str] = None


class CandidateAnswer(BaseModel):
    """One blog proposed by the discovery prompt, identified by a recent post."""
    post_url: str = Field(validation_alias=AliasChoices("post_url", "url", "homepage"))
    country: Optional[str] = None
    language: Optional[str] = None


Answer = TypeVar("Answer", bound=BaseModel)


def parse_json_array(text: str) -> list[Any]:
    """Extracts the JSON array from a model answer that may contain code fences or other text.

    Args:
        text: The answer.

    Returns:
        list: The parsed array.

    Raises:
        ValueError: If the answer contains no valid JSON array.
    """
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end < start:
        raise ValueError("the answer contains no JSON array")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("the answer is not a JSON array")
    return data


def parse_answers(text: str, model: type[Answer]) -> list[Answer]:
    """Parses a JSON array answer into models; invalid items are skipped.

    Args:
        text: The answer.
        model: The model of one item.

    Returns:
        list: The valid items.

    Raises:
        ValueError: If the answer contains no valid JSON array.
    """
    items = []
    for item in parse_json_array(text):
        try:
            items.append(model.model_validate(item))
        except ValidationError:
            logger.info("Skipped an invalid answer item: %r", item)
    return items


def valid_country(value: Optional[str]) -> Optional[str]:
    """Returns an ISO 3166-1 alpha-2 code in upper case, or None for anything else.

    Common aliases such as "UK" are mapped to their ISO codes.
    """
    code = (value or "").strip().upper()
    if len(code) != 2 or not code.isascii() or not code.isalpha():
        return None
    return COUNTRY_ALIASES.get(code, code)


MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December")


def _month_name(day: date) -> str:
    """Returns for example "October 2026", independent of the system locale."""
    return f"{MONTH_NAMES[day.month - 1]} {day.year}"


def weekly_focus(today: date) -> str:
    """Returns the search focus of the ISO week of a date."""
    return SEARCH_FOCUS[today.isocalendar().week % len(SEARCH_FOCUS)]


def _is_excluded_host(host: str) -> bool:
    """Returns True for hosts that are never personal blogs."""
    return any(host == domain or host.endswith("." + domain) for domain in EXCLUDED_CANDIDATE_DOMAINS)


def recheck_paused_sources(registry: SourceRegistry, today: date, fetch: Fetcher = http_get) -> list[tuple[str, str, str]]:
    """Fetches the feeds of dormant and broken sources and applies the lifecycle rules.

    Broken sources without a known feed are searched for a feed again.

    Args:
        registry: The source registry.
        today: Date of the run.
        fetch: Fetcher function.

    Returns:
        list: ``(source_id, old status, new status)`` for every change.
    """
    def check(source: SourceEntry) -> tuple[SourceEntry, Optional[ParsedFeed]]:
        if source.feed_url:
            try:
                return source, fetch_feed(source.feed_url, fetch)
            except FeedError as e:
                logger.info("Feed of %s still failing: %s", source.source_id, e)
                return source, None
        return source, find_feed(source.homepage, fetch).feed

    paused = registry.with_status("dormant", "broken")
    with ThreadPoolExecutor(max_workers=RECHECK_WORKERS) as pool:
        results = list(pool.map(check, paused))
    for source, feed in results:
        registry.put(record_feed_result(source, feed, today))
    return registry.apply_rules(today)


def lookup_countries(registry: SourceRegistry, model: Any, today: date) -> int:
    """Asks Gemini for the country and language of sources not yet looked up.

    Sources whose country comes from Gemini are skipped, and so are sources
    looked up within LOOKUP_REPEAT_DAYS, including those Gemini could not place.

    Args:
        registry: The source registry.
        model: A ``SearchModel`` or a compatible test double.
        today: Date of the run.

    Returns:
        int: Number of sources whose country was set.
    """
    due = [
        entry for entry in registry.with_status("active", "probation", "dormant", "broken")
        if entry.country_basis != "model"
        and (entry.country_lookup is None or (today - entry.country_lookup).days >= LOOKUP_REPEAT_DAYS)
    ]
    updated = 0
    for start in range(0, len(due), LOOKUP_BATCH_SIZE):
        batch = due[start:start + LOOKUP_BATCH_SIZE]
        prompt = COUNTRY_PROMPT.format(domains="\n".join(entry.source_id for entry in batch))
        try:
            answers = {
                answer.domain.strip().lower().removeprefix("www."): answer
                for answer in parse_answers(model.ask(prompt, COUNTRY_LOOKUP_TEMPERATURE).text, CountryAnswer)
            }
        except (TransientExtractionError, ValueError) as e:
            logger.warning("Country lookup for %d sources failed: %s", len(batch), e)
            continue
        for entry in batch:
            answer = answers.get(entry.source_id)
            update: dict[str, Any] = {"country_lookup": today}
            country = valid_country(answer.country) if answer else None
            if country:
                update.update(country=country, country_basis="model")
                updated += 1
            # The feed's language tag is often the WordPress default, so the model's answer takes precedence
            language = language_code(answer.language) if answer else None
            if language:
                update["language"] = language
            registry.put(entry.model_copy(update=update))
    return updated


def discover_sources(registry: SourceRegistry, model: Any, today: date, fetch: Fetcher = http_get,
                     focus: Optional[str] = None) -> Counter:
    """Asks Gemini for new blogs and admits those that pass the admission rules.

    Candidates are the blogs named in the answer and the domains of the search
    results behind the answer. Every evaluated candidate is stored, also when it
    is rejected, so that it is not proposed again. At most MAX_CANDIDATES_EVALUATED
    candidates are evaluated and at most MAX_ADMISSIONS_PER_WEEK admitted.

    Args:
        registry: The source registry.
        model: A ``SearchModel`` or a compatible test double.
        today: Date of the run.
        fetch: Fetcher function.
        focus: Search focus; defaults to the focus of the week.

    Returns:
        Counter: Candidates by result.
    """
    summary: Counter = Counter()
    if registry.admitted_count() >= MAX_ADMITTED_SOURCES:
        summary["registry full"] += 1
        return summary
    known = ", ".join(sorted(entry.source_id for entry in registry)) or "(none)"
    prompt = DISCOVERY_PROMPT.format(
        current_month=_month_name(today),
        previous_month=_month_name(today.replace(day=1) - timedelta(days=1)),
        focus=focus or weekly_focus(today),
        count=CANDIDATES_PER_RUN,
        excluded=known,
    )
    try:
        answer = model.ask(prompt, DISCOVERY_TEMPERATURE)
    except TransientExtractionError as e:
        logger.warning("Discovery failed: %s", e)
        summary["no answer"] += 1
        return summary
    candidates: list[CandidateAnswer] = []
    try:
        candidates = parse_answers(answer.text, CandidateAnswer)
    except ValueError as e:
        logger.warning("Discovery answer without a JSON list (%s): %r", e, answer.text[:300])
        summary["answer without list"] += 1
    # The domains of the search results are candidates too, also when the answer text is unusable
    proposed = set()
    for candidate in candidates:
        try:
            proposed.add(domain_id(candidate.post_url))
        except ValueError:
            continue
    for domain in answer.source_domains:
        if domain not in proposed:
            candidates.append(CandidateAnswer(post_url=f"https://{domain}/"))
            summary["from search sources"] += 1
    admitted = 0
    for candidate in candidates[:MAX_CANDIDATES_EVALUATED]:
        if admitted >= MAX_ADMISSIONS_PER_WEEK:
            summary["weekly limit reached"] += 1
            break
        url = candidate.post_url.strip()
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            summary["invalid url"] += 1
            continue
        try:
            source_id = domain_id(url)
        except ValueError:
            summary["invalid url"] += 1
            continue
        if source_id in registry:
            summary["known"] += 1
            continue
        if _is_excluded_host(source_id):
            summary["not a blog"] += 1
            continue
        # The post page links the blog's feed; the registry keeps the blog's root as homepage
        entry = evaluate_candidate(url, "discovery", today, fetch)
        country = valid_country(candidate.country)
        language = language_code(candidate.language)
        update: dict[str, Any] = {"homepage": f"{parts.scheme}://{parts.netloc}/"}
        if country:
            update.update(country=country, country_basis="model", country_lookup=today)
        if language:
            update["language"] = language
        entry = entry.model_copy(update=update)
        if not registry.admit(entry):
            summary["registry full"] += 1
            break
        summary[entry.status] += 1
        if entry.status in ADMITTED_STATUSES:
            admitted += 1
    return summary


def weekly_maintenance(settings: Settings, today: date, model: Any, fetch: Fetcher = http_get,
                       legacy_dir: Path = LEGACY_DATA_DIR) -> dict[str, Any]:
    """Runs the weekly maintenance and saves the registry.

    Args:
        settings: The run settings.
        today: UTC date of the run.
        model: A ``SearchModel`` or a compatible test double.
        fetch: Fetcher function.
        legacy_dir: Folder with the legacy records, used to seed an empty registry.

    Returns:
        dict: Summary of the run, also appended to the run log.
    """
    registry = SourceRegistry.load(settings.state_dir)
    if len(registry) == 0:
        seed_from_legacy(registry, legacy_dir, today, fetch)
    summary: dict[str, Any] = {"date": today.isoformat(), "mode": "maintenance"}
    changes = recheck_paused_sources(registry, today, fetch)
    summary["source_transitions"] = [{"source": s, "from": old, "to": new} for s, old, new in changes]
    registry.save()
    try:
        summary["countries_set"] = lookup_countries(registry, model, today)
        registry.save()
        summary["candidates"] = dict(discover_sources(registry, model, today, fetch))
    except RunStopError as e:
        logger.warning("Weekly maintenance stopped: %s", e)
        summary["stopped_early"] = f"{type(e).__name__}: {e}"
        summary["fatal"] = isinstance(e, FatalRequestError)
    registry.save()
    summary["gemini_calls"] = model.calls
    summary["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    append_run_log(settings.state_dir, summary)
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    """Runs the weekly maintenance once.

    Args:
        argv: Arguments without the program name; None reads ``sys.argv``.

    Returns:
        int: 0 on success, 1 if the Gemini API rejected requests, 2 if the API key is missing.
    """
    parser = argparse.ArgumentParser(description="Weekly maintenance of the source registry.")
    parser.add_argument("--output-dir", type=Path,
                        help="folder with the state (default: data/ in GitHub Actions, ~/Downloads/style_dataset locally)")
    args = parser.parse_args(argv)
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    settings = Settings.from_env()
    if args.output_dir is not None:
        settings = replace(settings, output_dir=args.output_dir)
    try:
        model = SearchModel(settings)
    except ValueError as e:
        logger.error("Cannot start the weekly maintenance: %s", e)
        return 2
    summary = weekly_maintenance(settings, datetime.now(timezone.utc).date(), model)
    logger.info("Weekly maintenance finished: %s", json.dumps(summary))
    return 1 if summary.get("fatal") else 0


if __name__ == "__main__":
    sys.exit(main())
