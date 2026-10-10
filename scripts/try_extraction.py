"""
Runs the Gemini extraction on a fixed set of posts and writes an HTML review sheet.

Development tool for checking the schema and the prompt on real posts; the
daily pipeline does not use it. Images are saved to the output folder only,
never to the dataset.

The input is a JSON file with a list of posts:

    [
      {
        "url": "https://example.com/2026/10/fall-outfit",
        "source_id": "example.com",
        "title": "Fall outfit",
        "published_date": "2026-10-08",
        "tags": ["Outfits"],
        "text": "Today I am wearing ...",
        "link_texts": ["J.Crew cardigan"],
        "images": [{"url": "https://example.com/1.jpg", "alt": "...", "caption": "..."}]
      }
    ]

Usage:
    uv run python scripts/try_extraction.py posts.json --out extraction_review
"""
import argparse
import hashlib
import html
import io
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dotenv import load_dotenv  # noqa: E402
from PIL import Image  # noqa: E402

from extraction import (  # noqa: E402
    ExtractionError,
    ExtractionResult,
    GeminiExtractor,
    ImageInput,
    PostContext,
    RunStopError,
    extraction_hash,
)
from parser import download_and_resize_image  # noqa: E402
from records import PostMetadata, SourceMetadata, build_records, is_excluded  # noqa: E402
from schema import ImageRef, OutfitRecord  # noqa: E402
from settings import Settings  # noqa: E402

logger = logging.getLogger("try_extraction")


@dataclass
class ReviewEntry:
    """Result of one post for the review sheet.

    Attributes:
        post (dict): The input post.
        image_files (list[str]): Saved images, relative to the output folder.
        result (Optional[ExtractionResult]): The extraction, if it succeeded.
        records (list[OutfitRecord]): Records built from the extraction.
        error (Optional[str]): Error message, if the extraction failed.
        seconds (float): Duration of the extraction.
        calls (int): Gemini calls used for this post.
        tokens (int): Tokens used for this post.
    """
    post: dict[str, Any]
    image_files: list[str] = field(default_factory=list)
    result: Optional[ExtractionResult] = None
    records: list[OutfitRecord] = field(default_factory=list)
    error: Optional[str] = None
    seconds: float = 0.0
    calls: int = 0
    tokens: int = 0


def load_posts(path: Path) -> list[dict[str, Any]]:
    """Reads the input posts.

    Args:
        path: Path of the JSON file.

    Returns:
        list[dict]: The posts.

    Raises:
        ValueError: If the file does not contain a JSON list.
    """
    posts = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(posts, list):
        raise ValueError(f"{path} must contain a JSON list of posts")
    return posts


def prepare_images(
    post: dict[str, Any], post_number: int, out_dir: Path, max_images: int
) -> tuple[list[ImageInput], list[ImageRef], list[str]]:
    """Downloads, resizes and saves the images of a post.

    Args:
        post: The input post.
        post_number: Number of the post, used in the file names.
        out_dir: The output folder.
        max_images: Maximum number of images to keep.

    Returns:
        tuple: Model inputs, image references and saved file paths, in the same order.
    """
    inputs: list[ImageInput] = []
    refs: list[ImageRef] = []
    files: list[str] = []
    for number, image in enumerate(post.get("images", []), start=1):
        if len(inputs) >= max_images:
            logger.warning("Post %d: more than %d images; the rest is skipped.", post_number, max_images)
            break
        jpeg = download_and_resize_image(image["url"])
        if jpeg is None:
            continue
        file_name = f"post{post_number:02d}_img{number:02d}.jpg"
        (out_dir / "images" / file_name).write_bytes(jpeg)
        with Image.open(io.BytesIO(jpeg)) as img:
            width, height = img.size
        inputs.append(ImageInput(jpeg=jpeg, alt=image.get("alt"), caption=image.get("caption")))
        # Hash of the JPEG bytes; sufficient to tell the images of this review apart
        refs.append(ImageRef(hash=hashlib.sha256(jpeg).hexdigest()[:16], width=width, height=height))
        files.append(f"images/{file_name}")
    return inputs, refs, files


def _image_tags(files: list[str], indexes: list[int]) -> str:
    """Renders thumbnails for 1-based image numbers.

    Args:
        files: Saved image files in request order.
        indexes: Image numbers to show.

    Returns:
        str: HTML image tags.
    """
    return "".join(
        f'<img src="{html.escape(files[i - 1])}" alt="Image {i}" title="Image {i}">'
        for i in indexes if 1 <= i <= len(files)
    )


def render_review(entries: list[ReviewEntry], header: str) -> str:
    """Renders the review sheet.

    Args:
        entries: Results per post.
        header: Summary line shown at the top.

    Returns:
        str: A self-contained HTML page.
    """
    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Extraction review</title><style>",
        "body{font-family:system-ui,sans-serif;margin:16px;color:#1a1a1a;background:#fff}",
        ".post{border-top:2px solid #ccc;margin-top:24px;padding-top:12px}",
        "img{height:220px;margin:0 6px 6px 0;border:1px solid #ddd}",
        ".outfit{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start;margin:12px 0}",
        "pre{flex:1;min-width:300px;max-height:460px;overflow:auto;background:#f4f4f4;padding:8px;font-size:12px}",
        ".meta{color:#555;font-size:13px}.error{color:#a00}",
        "</style></head><body><h1>Extraction review</h1>",
        f"<p class=\"meta\">{html.escape(header)}</p>",
    ]
    for entry in entries:
        post = entry.post
        parts.append("<section class=\"post\">")
        parts.append(f"<h2>{html.escape(post.get('title') or post['url'])}</h2>")
        parts.append(
            f"<p class=\"meta\"><a href=\"{html.escape(post['url'])}\">{html.escape(post['url'])}</a> | "
            f"{len(entry.image_files)} images | {entry.seconds:.1f} s | {entry.calls} calls | {entry.tokens} tokens</p>"
        )
        if entry.error:
            parts.append(f"<p class=\"error\">{html.escape(entry.error)}</p>")
            parts.append(_image_tags(entry.image_files, list(range(1, len(entry.image_files) + 1))))
        if entry.result is not None:
            extraction = entry.result.extraction
            parts.append(f"<details><summary>Visual analysis</summary><pre>{html.escape(extraction.visual_analysis)}</pre></details>")
            records = iter(entry.records)
            for outfit in extraction.outfits:
                parts.append("<div class=\"outfit\"><div>")
                parts.append(_image_tags(entry.image_files, outfit.image_indexes))
                parts.append("</div>")
                if is_excluded(outfit):
                    parts.append(f"<p>Not stored (age group {html.escape(outfit.age_group)}).</p></div>")
                    continue
                record = next(records)
                parts.append(f"<pre>{html.escape(json.dumps(record.model_dump(), indent=2))}</pre></div>")
            for item in extraction.rejected_images:
                parts.append(f"<p>Rejected image {item.index}: {html.escape(item.reason)}</p>")
                parts.append(_image_tags(entry.image_files, [item.index]))
            for label, indexes in (("Unassigned", entry.result.unassigned_indexes), ("Unusable", entry.result.unusable_indexes)):
                if indexes:
                    parts.append(f"<p>{label} images: {indexes}</p>{_image_tags(entry.image_files, indexes)}")
        parts.append("</section>")
    parts.append("</body></html>")
    return "".join(parts)


def parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    """Parses the command-line arguments.

    Args:
        argv: Arguments without the program name; None reads ``sys.argv``.

    Returns:
        argparse.Namespace: The parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("posts", type=Path, help="JSON file with the posts to extract")
    parser.add_argument("--out", type=Path, default=Path("extraction_review"), help="Output folder")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Runs the extraction for all posts of the input file.

    Args:
        argv: Arguments without the program name; None reads ``sys.argv``.

    Returns:
        int: Exit code.
    """
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    load_dotenv()
    settings = Settings.from_env()
    (args.out / "images").mkdir(parents=True, exist_ok=True)
    extractor = GeminiExtractor(settings)
    config_hash = extraction_hash(settings)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    entries: list[ReviewEntry] = []
    all_records: list[dict[str, Any]] = []
    for number, post in enumerate(load_posts(args.posts), start=1):
        entry = ReviewEntry(post=post)
        entries.append(entry)
        inputs, refs, entry.image_files = prepare_images(post, number, args.out, settings.max_images_per_post)
        if not inputs:
            entry.error = "No image could be downloaded."
            continue
        context = PostContext(
            title=post.get("title"),
            published_date=post.get("published_date"),
            tags=tuple(post.get("tags", [])),
            text=post.get("text", ""),
            link_texts=tuple(post.get("link_texts", [])),
        )
        calls_before, tokens_before = extractor.usage.calls, extractor.usage.total_tokens
        started = time.monotonic()
        try:
            entry.result = extractor.extract(context, inputs)
        except ExtractionError as e:
            entry.error = f"{type(e).__name__}: {e}"
        except RunStopError as e:
            entry.error = f"{type(e).__name__}: {e}"
            logger.error("Stopping: %s", e)
            break
        finally:
            entry.seconds = time.monotonic() - started
            entry.calls = extractor.usage.calls - calls_before
            entry.tokens = extractor.usage.total_tokens - tokens_before
        if entry.result is None:
            continue
        entry.records = build_records(
            entry.result.extraction,
            refs,
            PostMetadata(
                url=post["url"],
                canonical_url=None,
                title=post.get("title"),
                published_date=post.get("published_date"),
                tags=tuple(post.get("tags", [])),
                shopping_link_count=len(post.get("link_texts", [])),
            ),
            SourceMetadata(source_id=post.get("source_id", ""), country=None, country_basis="unknown", language=None),
            model=settings.gemini_model,
            extraction_hash=config_hash,
            date_scraped=today,
        )
        all_records.extend(record.model_dump() for record in entry.records)

    usage = extractor.usage
    header = (
        f"Model {settings.gemini_model} | extraction hash {config_hash} | {len(all_records)} outfits | "
        f"{usage.calls} calls | tokens: prompt {usage.prompt_tokens}, output {usage.output_tokens}, "
        f"thinking {usage.thinking_tokens}, total {usage.total_tokens}"
    )
    (args.out / "records.json").write_text(json.dumps(all_records, indent=2), encoding="utf-8")
    (args.out / "review.html").write_text(render_review(entries, header), encoding="utf-8")
    logger.info("%s. Review sheet: %s", header, args.out / "review.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
