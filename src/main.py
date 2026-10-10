"""
Entry point of the daily pipeline run.

Usage:
    uv run python src/main.py [--dry-run] [--max-posts N] [--source DOMAIN ...] [--output-dir PATH]
"""
import argparse
import asyncio
import logging
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from crawl import create_crawler, fetch_post
from extraction import GeminiExtractor
from pipeline import DailyRun, RunStats, Services
from settings import Settings

logger = logging.getLogger(__name__)


def _positive_int(value: str) -> int:
    """Parses a command-line value as an integer of at least 1."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {number}")
    return number


def parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    """Parses the command-line arguments.

    Args:
        argv: Arguments without the program name; None reads ``sys.argv``.

    Returns:
        argparse.Namespace: The parsed arguments.
    """
    parser = argparse.ArgumentParser(description="Runs the daily fashion analytics pipeline.")
    parser.add_argument("--dry-run", action="store_true",
                        help="select posts and images without calling the model or saving state; writes a report")
    parser.add_argument("--max-posts", type=_positive_int,
                        help="maximum number of posts in this run (overrides MAX_POSTS_PER_RUN)")
    parser.add_argument("--source", action="append", dest="sources", metavar="DOMAIN",
                        help="process only this source; can be repeated")
    parser.add_argument("--output-dir", type=Path,
                        help="folder for records and state (default: data/ in GitHub Actions, "
                             "~/Downloads/style_dataset locally)")
    return parser.parse_args(argv)


async def execute(settings: Settings, args: argparse.Namespace, extractor: Optional[GeminiExtractor]) -> RunStats:
    """Runs the pipeline with a headless browser for the post pages.

    Args:
        settings: The run settings.
        args: The command-line arguments.
        extractor: The Gemini extractor; None in dry-run mode.

    Returns:
        RunStats: The counters of the run.
    """
    today = datetime.now(timezone.utc).date()
    async with create_crawler() as crawler:
        services = Services(fetch_page=lambda url: fetch_post(crawler, url), extractor=extractor)
        return await DailyRun(settings, today, services, dry_run=args.dry_run).run(source_ids=args.sources)


def main(argv: Optional[list[str]] = None) -> int:
    """Runs the pipeline once.

    Args:
        argv: Arguments without the program name; None reads ``sys.argv``.

    Returns:
        int: 0 on success, 1 if the Gemini API rejected requests, 2 if the API key is missing.
    """
    args = parse_args(argv)
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    settings = Settings.from_env()
    if args.output_dir is not None:
        settings = replace(settings, output_dir=args.output_dir)
    if args.max_posts is not None:
        settings = replace(settings, max_posts_per_run=args.max_posts)
    extractor = None
    if not args.dry_run:
        try:
            extractor = GeminiExtractor(settings)
        except ValueError as e:
            logger.error("Cannot start the run: %s", e)
            return 2
    logger.info("Output folder: %s", settings.output_dir.resolve())
    stats = asyncio.run(execute(settings, args, extractor))
    logger.info(
        "Run finished: %d posts selected from %d feeds; outcomes %s; %d outfits stored; usage %s.",
        stats.posts_selected, stats.feeds_ok, dict(stats.posts), stats.outfits, stats.usage,
    )
    if stats.fatal:
        logger.error("The Gemini API rejected requests: %s", stats.stopped_early)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
