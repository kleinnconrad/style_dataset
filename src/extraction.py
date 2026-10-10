"""
Extracts outfit data from the images of one blog post with Google Gemini.

One request covers all selected images of a post, so the model can group the
photos that show the same outfit. The module paces the requests, tells per-day
from per-minute quota errors, retries a blocked or invalid response once with
the images split into two requests, and cleans the validated response.
"""
import hashlib
import importlib.metadata
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

import httpx
from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from schema import OutfitExtraction, PostExtraction, describe_models
from settings import Settings

logger = logging.getLogger(__name__)

TEMPERATURE = 0.2
# Caps the hidden reasoning; visual_analysis already makes the model describe the images first
THINKING_BUDGET: Optional[int] = 2048
# High resolution keeps details such as fabric texture and necklines visible to the model
MEDIA_RESOLUTION = types.MediaResolution.MEDIA_RESOLUTION_HIGH
REQUEST_TIMEOUT_MS = 300_000
# The SDK retries these codes itself. 429 is handled here to tell per-day from per-minute quotas.
SDK_RETRY_STATUS_CODES = [408, 500, 502, 503, 504]
SDK_RETRY_ATTEMPTS = 4
MIN_CALL_INTERVAL_S = 6.0
PER_MINUTE_QUOTA_RETRIES = 3
DEFAULT_QUOTA_WAIT_S = 60.0
MAX_QUOTA_WAIT_S = 120.0
MAX_TEXT_CHARS = 8_000
MAX_LINK_TEXTS = 40
MAX_IMAGE_CONTEXT_CHARS = 300
# Image preprocessing; part of the extraction hash
IMAGE_MAX_SIDE_PX = 1024
IMAGE_JPEG_QUALITY = 85

SYSTEM_INSTRUCTION = """\
You extract structured fashion data from the photos of one blog post.

Rules:
1. Describe only what is visible in the images. If an attribute of the person or the photo cannot be seen, use "Not visible". For garment attributes that do not apply to the item, use "Not applicable". Use null for free-text fields you cannot fill.
2. An outfit is one person wearing one set of clothes. Put all images that show the same person in the same clothes into one outfit, even if angle or crop differ. The same person in different clothes is a separate outfit. Two people in one image are two outfits.
3. Do not create outfits for images without a person wearing the clothes, such as product photos, flat lays, collages or close-ups of single items. List these images in rejected_images.
4. List each visible garment, shoe and accessory of an outfit once, with the closest garment_type.
5. Use "Not visible" for the weather of indoor photos and whenever the sky or the conditions cannot be seen.
6. Name a brand only if it appears in the post title, text, tags, image captions or link texts. Do not infer brands from the look of an item.
7. The post text can help to identify items, but do not describe items that are not visible in the images.
"""

IMAGE_LABEL_TEMPLATE = "Image {number}{context}:"

PROMPT_TEMPLATE = """\
The {image_count} images above are from one blog post.

Post title: {title}
Published: {published}
Tags: {tags}
Link texts in the post: {link_texts}

Post text (may be shortened):
{text}

Fill the response schema for these images. Start with visual_analysis."""


class ExtractionError(Exception):
    """Base class for extraction failures of a single post."""


class UnusableResponseError(ExtractionError):
    """The model blocked the request or returned an invalid response, also after splitting the images."""


class TransientExtractionError(ExtractionError):
    """A server error or timeout persisted after the SDK retries. The post can be retried in a later run."""


class RunStopError(Exception):
    """Base class for conditions that end extraction for the whole run."""


class QuotaExhaustedError(RunStopError):
    """The per-day quota is exhausted, or the per-minute quota did not recover after several waits."""


class CallBudgetExceededError(RunStopError):
    """The run reached MAX_GEMINI_CALLS_PER_RUN."""


class FatalRequestError(RunStopError):
    """The API rejected a request with a client error other than 429, which points to a problem with the key, model or schema."""


class _UnusableResponse(Exception):
    """Internal signal that a response cannot be used; triggers the split retry."""


@dataclass(frozen=True)
class ImageInput:
    """One image of a post, prepared for the model.

    Attributes:
        jpeg (bytes): The resized image as JPEG.
        alt (Optional[str]): Alt text of the image in the post.
        caption (Optional[str]): Caption of the image in the post.
    """
    jpeg: bytes
    alt: Optional[str] = None
    caption: Optional[str] = None


@dataclass(frozen=True)
class PostContext:
    """Text context of a post, sent along with its images.

    Attributes:
        title (Optional[str]): Title of the post.
        published_date (Optional[str]): Publish date (YYYY-MM-DD).
        tags (tuple[str, ...]): Categories and tags from the feed.
        text (str): Text of the post.
        link_texts (tuple[str, ...]): Texts of the external links in the post.
    """
    title: Optional[str]
    published_date: Optional[str]
    tags: tuple[str, ...] = ()
    text: str = ""
    link_texts: tuple[str, ...] = ()


@dataclass
class UsageTotals:
    """Number of calls and tokens used in a run.

    Attributes:
        calls (int): Requests sent, including retries.
        prompt_tokens (int): Input tokens, including image tokens.
        output_tokens (int): Tokens of the visible response.
        thinking_tokens (int): Tokens the model used for thinking.
        total_tokens (int): All tokens as reported by the API.
    """
    calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    total_tokens: int = 0

    def add(self, usage_metadata: Any) -> None:
        """Adds the token counts of one response.

        Args:
            usage_metadata: The ``usage_metadata`` of a response; may be None.
        """
        if usage_metadata is None:
            return
        self.prompt_tokens += getattr(usage_metadata, "prompt_token_count", None) or 0
        self.output_tokens += getattr(usage_metadata, "candidates_token_count", None) or 0
        self.thinking_tokens += getattr(usage_metadata, "thoughts_token_count", None) or 0
        self.total_tokens += getattr(usage_metadata, "total_token_count", None) or 0


@dataclass
class ExtractionResult:
    """The cleaned extraction of one post.

    Attributes:
        extraction (PostExtraction): Outfits and rejected images. Image numbers
            refer to the order of the images passed to ``extract``, starting at 1.
        unassigned_indexes (list[int]): Images the model neither assigned to an
            outfit nor rejected.
        unusable_indexes (list[int]): Images whose part of a split request failed.
        split (bool): Whether the images had to be split into two requests.
    """
    extraction: PostExtraction
    unassigned_indexes: list[int] = field(default_factory=list)
    unusable_indexes: list[int] = field(default_factory=list)
    split: bool = False


def create_client(api_key: Optional[str] = None) -> genai.Client:
    """Creates the Gemini client with timeout and retry options.

    The synchronous client is used on purpose: the async client sends requests
    through aiohttp when it is installed (crawl4ai installs it), and the SDK
    does not retry aiohttp timeouts.

    Args:
        api_key: API key; defaults to the GEMINI_API_KEY environment variable.

    Returns:
        genai.Client: The configured client.

    Raises:
        ValueError: If no API key is available.
    """
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY is not set")
    return genai.Client(
        api_key=key,
        http_options=types.HttpOptions(
            timeout=REQUEST_TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(
                attempts=SDK_RETRY_ATTEMPTS,
                http_status_codes=SDK_RETRY_STATUS_CODES,
            ),
        ),
    )


def extraction_hash(settings: Settings) -> str:
    """Returns a hash of everything that influences the extraction result.

    Args:
        settings: The run settings.

    Returns:
        str: 16 hexadecimal digits.
    """
    parts = {
        "system_instruction": SYSTEM_INSTRUCTION,
        "prompt_template": PROMPT_TEMPLATE,
        "image_label_template": IMAGE_LABEL_TEMPLATE,
        "schema": describe_models(include_descriptions=True),
        "model": settings.gemini_model,
        "temperature": TEMPERATURE,
        "thinking_budget": THINKING_BUDGET,
        "media_resolution": MEDIA_RESOLUTION.value,
        "image_max_side_px": IMAGE_MAX_SIDE_PX,
        "image_jpeg_quality": IMAGE_JPEG_QUALITY,
        "max_images_per_post": settings.max_images_per_post,
        "max_text_chars": MAX_TEXT_CHARS,
        "max_link_texts": MAX_LINK_TEXTS,
        "google_genai": importlib.metadata.version("google-genai"),
    }
    payload = json.dumps(parts, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _shorten(text: Optional[str], limit: int) -> str:
    """Collapses whitespace and cuts a text to a maximum length.

    Args:
        text: The text; None is treated as empty.
        limit: Maximum number of characters.

    Returns:
        str: The shortened text.
    """
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 3].rstrip() + "..."


def _image_label(number: int, image: ImageInput) -> str:
    """Builds the text label placed before an image.

    Args:
        number: Number of the image, starting at 1.
        image: The image with its alt text and caption.

    Returns:
        str: For example ``Image 2 (alt text: "...", caption: "..."):``.
    """
    context = []
    if image.alt and image.alt.strip():
        context.append(f"alt text: {json.dumps(_shorten(image.alt, MAX_IMAGE_CONTEXT_CHARS))}")
    if image.caption and image.caption.strip():
        context.append(f"caption: {json.dumps(_shorten(image.caption, MAX_IMAGE_CONTEXT_CHARS))}")
    suffix = f" ({', '.join(context)})" if context else ""
    return IMAGE_LABEL_TEMPLATE.format(number=number, context=suffix)


def build_prompt(post: PostContext, image_count: int) -> str:
    """Builds the text prompt that follows the images.

    Args:
        post: Text context of the post.
        image_count: Number of images in the request.

    Returns:
        str: The prompt.
    """
    link_texts = [_shorten(text, 120) for text in post.link_texts if text and text.strip()][:MAX_LINK_TEXTS]
    text = post.text.strip()
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS].rstrip() + " [...]"
    return PROMPT_TEMPLATE.format(
        image_count=image_count,
        title=post.title or "(none)",
        published=post.published_date or "(unknown)",
        tags=", ".join(post.tags) or "(none)",
        link_texts="; ".join(link_texts) or "(none)",
        text=text or "(none)",
    )


def build_contents(post: PostContext, images: Sequence[ImageInput]) -> list[Any]:
    """Builds the request contents: a label and the image for each image, then the prompt.

    Args:
        post: Text context of the post.
        images: The images, in the order in which they are numbered.

    Returns:
        list: Strings and image parts for ``generate_content``.
    """
    contents: list[Any] = []
    for number, image in enumerate(images, start=1):
        contents.append(_image_label(number, image))
        contents.append(types.Part.from_bytes(data=image.jpeg, mime_type="image/jpeg"))
    contents.append(build_prompt(post, len(images)))
    return contents


def _parse_duration_s(value: Any) -> Optional[float]:
    """Parses a protobuf duration such as ``"43s"`` or ``"1.5s"``.

    Args:
        value: The duration string.

    Returns:
        Optional[float]: Seconds, or None if the value cannot be parsed.
    """
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)s\s*", value)
    return float(match.group(1)) if match else None


def classify_quota_error(error: errors.APIError) -> tuple[bool, Optional[float]]:
    """Reads the details of a 429 error.

    Args:
        error: The API error.

    Returns:
        tuple[bool, Optional[float]]: Whether a per-day quota is exhausted, and
        the suggested wait in seconds, if the response contains one.
    """
    body = error.details if isinstance(error.details, dict) else {}
    error_body = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
    per_day = False
    retry_after = None
    for item in error_body.get("details") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("@type", ""))
        if kind.endswith("QuotaFailure"):
            for violation in item.get("violations") or []:
                if isinstance(violation, dict) and "PerDay" in str(violation.get("quotaId", "")):
                    per_day = True
        elif kind.endswith("RetryInfo"):
            retry_after = _parse_duration_s(item.get("retryDelay"))
    return per_day, retry_after


def parse_response(response: Any) -> PostExtraction:
    """Validates a response and returns the extraction.

    Args:
        response: The ``GenerateContentResponse``.

    Returns:
        PostExtraction: The validated model output.

    Raises:
        _UnusableResponse: If the prompt was blocked, generation did not finish
            normally, the text is empty, or the JSON does not match the schema.
    """
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        raise _UnusableResponse(f"prompt blocked ({feedback.block_reason})")
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise _UnusableResponse("no candidates")
    finish_reason = getattr(candidates[0], "finish_reason", None)
    if finish_reason is not None and finish_reason != types.FinishReason.STOP:
        raise _UnusableResponse(f"finish reason {finish_reason}")
    text = getattr(response, "text", None)
    if not text:
        raise _UnusableResponse("empty response text")
    try:
        return PostExtraction.model_validate_json(text)
    except ValidationError as e:
        raise _UnusableResponse(f"response failed validation ({e.error_count()} errors)") from e


def _clean_text(value: Optional[str]) -> Optional[str]:
    """Collapses whitespace and turns empty text into None.

    Args:
        value: Free text from the model.

    Returns:
        Optional[str]: The cleaned text or None.
    """
    cleaned = " ".join((value or "").split())
    return cleaned or None


def _verified_brands(brands: Sequence[str], reference_text: str) -> list[str]:
    """Keeps the brands that appear as whole words in the post's own text.

    Args:
        brands: Brand names returned by the model.
        reference_text: Title, text, tags, link texts, alt texts and captions of the post.

    Returns:
        list[str]: The verified brands without case-insensitive duplicates.
    """
    verified: dict[str, str] = {}
    for brand in brands:
        name = " ".join(brand.split())
        if len(name) < 2 or name.casefold() in verified:
            continue
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", reference_text, flags=re.IGNORECASE):
            verified[name.casefold()] = name
        else:
            logger.debug("Dropped brand %r: not found in the post text.", name)
    return list(verified.values())


def _restrict_indexes(extraction: PostExtraction, image_count: int) -> PostExtraction:
    """Removes image numbers outside 1..image_count from outfits and rejected images.

    Args:
        extraction: The model output.
        image_count: Number of images in the request.

    Returns:
        PostExtraction: A copy with valid image numbers only.
    """
    valid = range(1, image_count + 1)
    outfits = [
        outfit.model_copy(update={"image_indexes": [i for i in outfit.image_indexes if i in valid]})
        for outfit in extraction.outfits
    ]
    rejected = [item for item in extraction.rejected_images if item.index in valid]
    return extraction.model_copy(update={"outfits": outfits, "rejected_images": rejected})


def _shift_indexes(extraction: PostExtraction, offset: int) -> PostExtraction:
    """Adds an offset to all image numbers, to map a split request back to the full post.

    Args:
        extraction: The output of a split request.
        offset: Number of images before this part.

    Returns:
        PostExtraction: A copy with shifted image numbers.
    """
    outfits = [
        outfit.model_copy(update={"image_indexes": [i + offset for i in outfit.image_indexes]})
        for outfit in extraction.outfits
    ]
    rejected = [item.model_copy(update={"index": item.index + offset}) for item in extraction.rejected_images]
    return extraction.model_copy(update={"outfits": outfits, "rejected_images": rejected})


def _clean_outfit(outfit: OutfitExtraction, reference_text: str) -> OutfitExtraction:
    """Normalizes the free text, lists and the focal index of one outfit.

    Args:
        outfit: An outfit with valid image numbers.
        reference_text: The post's own text, used to verify brands.

    Returns:
        OutfitExtraction: The cleaned outfit.
    """
    focal = outfit.focal_garment_index
    if focal is not None and not 1 <= focal <= len(outfit.garments):
        focal = None
    garments = [
        garment.model_copy(update={
            "description": _clean_text(garment.description),
            "design_details": list(dict.fromkeys(garment.design_details)),
        })
        for garment in outfit.garments
    ]
    return outfit.model_copy(update={
        "image_indexes": sorted(set(outfit.image_indexes)),
        "focal_garment_index": focal,
        "style_detail": _clean_text(outfit.style_detail),
        "aesthetic_other": _clean_text(outfit.aesthetic_other),
        "aesthetic_tags": list(dict.fromkeys(outfit.aesthetic_tags)),
        "brand_mentions": _verified_brands(outfit.brand_mentions, reference_text),
        "garments": garments,
    })


def clean_extraction(
    extraction: PostExtraction,
    image_count: int,
    reference_text: str,
    unusable_indexes: Sequence[int] = (),
) -> tuple[PostExtraction, list[int]]:
    """Validates the image numbers and normalizes the outfits of a response.

    Args:
        extraction: The model output for the whole post.
        image_count: Number of images of the post.
        reference_text: The post's own text, used to verify brands.
        unusable_indexes: Images of a failed part of a split request.

    Returns:
        tuple[PostExtraction, list[int]]: The cleaned extraction and the images
        that were neither assigned to an outfit nor rejected.
    """
    restricted = _restrict_indexes(extraction, image_count)
    outfits = []
    for outfit in restricted.outfits:
        if not outfit.image_indexes:
            logger.info("Dropped an outfit without valid image numbers.")
            continue
        outfits.append(_clean_outfit(outfit, reference_text))
    assigned = {index for outfit in outfits for index in outfit.image_indexes}
    rejected = {}
    for item in restricted.rejected_images:
        if item.index not in assigned and item.index not in rejected:
            rejected[item.index] = item
    unassigned = sorted(set(range(1, image_count + 1)) - assigned - set(rejected) - set(unusable_indexes))
    cleaned = restricted.model_copy(update={
        "outfits": outfits,
        "rejected_images": [rejected[index] for index in sorted(rejected)],
    })
    return cleaned, unassigned


def reference_text(post: PostContext, images: Sequence[ImageInput]) -> str:
    """Joins all text of a post in which brand names may appear.

    Args:
        post: Text context of the post.
        images: The images with alt texts and captions.

    Returns:
        str: The joined text.
    """
    pieces = [post.title or "", post.text, *post.tags, *post.link_texts]
    for image in images:
        pieces.extend([image.alt or "", image.caption or ""])
    return "\n".join(piece for piece in pieces if piece)


class GeminiExtractor:
    """Sends the images of a post to Gemini and returns the cleaned outfits.

    The extractor counts calls and tokens over a run and keeps a minimum
    interval between calls. It is synchronous; async callers use
    ``asyncio.to_thread``.

    Attributes:
        usage (UsageTotals): Calls and tokens of the run so far.
    """

    def __init__(
        self,
        settings: Settings,
        client: Optional[Any] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initializes the extractor.

        Args:
            settings: The run settings.
            client: A ``genai.Client`` or a compatible test double; created from
                GEMINI_API_KEY if omitted.
            sleep: Function used to wait; replaceable in tests.
            clock: Monotonic clock in seconds; replaceable in tests.
        """
        self._settings = settings
        self._client = client if client is not None else create_client()
        self._sleep = sleep
        self._clock = clock
        self._last_call_at: Optional[float] = None
        self._config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=PostExtraction,
            temperature=TEMPERATURE,
            media_resolution=MEDIA_RESOLUTION,
            thinking_config=(
                types.ThinkingConfig(thinking_budget=THINKING_BUDGET) if THINKING_BUDGET is not None else None
            ),
        )
        self.usage = UsageTotals()

    def extract(self, post: PostContext, images: Sequence[ImageInput]) -> ExtractionResult:
        """Extracts the outfits shown in the images of one post.

        Args:
            post: Text context of the post.
            images: The selected images, at most ``max_images_per_post``.

        Returns:
            ExtractionResult: The cleaned extraction.

        Raises:
            ValueError: If no images or too many images are passed.
            UnusableResponseError: If no usable response was obtained, also after splitting.
            TransientExtractionError: If a server error or timeout persisted.
            RunStopError: If the quota, the call budget or a client error ends the run.
        """
        if not images:
            raise ValueError("extract() needs at least one image")
        if len(images) > self._settings.max_images_per_post:
            raise ValueError(f"{len(images)} images exceed MAX_IMAGES_PER_POST ({self._settings.max_images_per_post})")
        context = reference_text(post, images)
        try:
            extraction = self._request(post, images)
        except _UnusableResponse as e:
            if len(images) < 2:
                raise UnusableResponseError(str(e)) from e
            logger.warning("Unusable response for %d images (%s). Retrying with the images split into two requests.", len(images), e)
            return self._extract_split(post, images, context)
        cleaned, unassigned = clean_extraction(extraction, len(images), context)
        return ExtractionResult(extraction=cleaned, unassigned_indexes=unassigned)

    def _extract_split(self, post: PostContext, images: Sequence[ImageInput], context: str) -> ExtractionResult:
        """Sends the two halves of the images as separate requests and merges the results.

        Args:
            post: Text context of the post.
            images: All images of the post.
            context: The post's own text, used to verify brands.

        Returns:
            ExtractionResult: The merged extraction. Images of a failed half are
            reported as unusable.

        Raises:
            UnusableResponseError: If both halves fail.
        """
        middle = (len(images) + 1) // 2
        analyses: list[str] = []
        outfits: list[OutfitExtraction] = []
        rejected = []
        unusable: list[int] = []
        for offset, part in ((0, images[:middle]), (middle, images[middle:])):
            try:
                partial = self._request(post, part)
            except _UnusableResponse as e:
                logger.warning("Images %d-%d are still unusable after the split (%s).", offset + 1, offset + len(part), e)
                unusable.extend(range(offset + 1, offset + len(part) + 1))
                continue
            shifted = _shift_indexes(_restrict_indexes(partial, len(part)), offset)
            analyses.append(shifted.visual_analysis)
            outfits.extend(shifted.outfits)
            rejected.extend(shifted.rejected_images)
        if len(unusable) == len(images):
            raise UnusableResponseError("both halves of the split request were unusable")
        merged = PostExtraction(visual_analysis="\n\n".join(analyses), outfits=outfits, rejected_images=rejected)
        cleaned, unassigned = clean_extraction(merged, len(images), context, unusable)
        return ExtractionResult(extraction=cleaned, unassigned_indexes=unassigned, unusable_indexes=unusable, split=True)

    def _request(self, post: PostContext, images: Sequence[ImageInput]) -> PostExtraction:
        """Sends one request and validates the response.

        Args:
            post: Text context of the post.
            images: The images of this request.

        Returns:
            PostExtraction: The validated output; image numbers refer to ``images``.

        Raises:
            _UnusableResponse: If the response cannot be used.
        """
        response = self._generate(build_contents(post, images))
        return parse_response(response)

    def _wait_for_slot(self) -> None:
        """Waits until MIN_CALL_INTERVAL_S has passed since the previous call."""
        if self._last_call_at is None:
            return
        remaining = MIN_CALL_INTERVAL_S - (self._clock() - self._last_call_at)
        if remaining > 0:
            self._sleep(remaining)

    def _generate(self, contents: list[Any]) -> Any:
        """Calls the model with budget, pacing and quota handling.

        Args:
            contents: The request contents.

        Returns:
            The ``GenerateContentResponse``.

        Raises:
            CallBudgetExceededError: If the call budget of the run is used up.
            QuotaExhaustedError: If the per-day quota is exhausted or the
                per-minute quota does not recover.
            FatalRequestError: For client errors other than 429.
            TransientExtractionError: For server errors and timeouts.
        """
        quota_waits = 0
        while True:
            if self.usage.calls >= self._settings.max_gemini_calls_per_run:
                raise CallBudgetExceededError(
                    f"MAX_GEMINI_CALLS_PER_RUN ({self._settings.max_gemini_calls_per_run}) reached"
                )
            self._wait_for_slot()
            self.usage.calls += 1
            try:
                response = self._client.models.generate_content(
                    model=self._settings.gemini_model, contents=contents, config=self._config
                )
            except errors.ClientError as e:
                if e.code != 429:
                    raise FatalRequestError(f"Gemini rejected the request: {e}") from e
                per_day, retry_after = classify_quota_error(e)
                if per_day:
                    raise QuotaExhaustedError(f"Per-day quota exhausted: {e.message}") from e
                if quota_waits >= PER_MINUTE_QUOTA_RETRIES:
                    raise QuotaExhaustedError(f"Quota still exhausted after {quota_waits} waits: {e.message}") from e
                quota_waits += 1
                wait = min(retry_after or DEFAULT_QUOTA_WAIT_S, MAX_QUOTA_WAIT_S)
                logger.warning("Per-minute quota reached. Waiting %.0f s before retry %d.", wait, quota_waits)
                self._sleep(wait)
                continue
            except errors.ServerError as e:
                raise TransientExtractionError(f"Gemini server error after retries: {e}") from e
            except httpx.TransportError as e:
                raise TransientExtractionError(f"Network error after retries: {e!r}") from e
            finally:
                self._last_call_at = self._clock()
            usage = getattr(response, "usage_metadata", None)
            self.usage.add(usage)
            logger.info(
                "Gemini call %d: prompt %s tokens, output %s, thinking %s.",
                self.usage.calls,
                getattr(usage, "prompt_token_count", None),
                getattr(usage, "candidates_token_count", None),
                getattr(usage, "thoughts_token_count", None),
            )
            return response
