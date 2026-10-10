"""
Downloads the image candidates of a post, filters them by real size and shape,
and removes duplicates with a perceptual hash (dHash).
"""
import io
import logging
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Optional

import requests
from PIL import Image, ImageOps

from content import ImageCandidate
from extraction import IMAGE_JPEG_QUALITY, IMAGE_MAX_SIDE_PX
from feeds import USER_AGENT
from state import DHASH_MAX_DISTANCE

logger = logging.getLogger(__name__)

MIN_SIDE_PX = 400
# Width divided by height; excludes banners and narrow strips
MIN_ASPECT_RATIO = 0.4
MAX_ASPECT_RATIO = 2.0
MAX_CANDIDATE_DOWNLOADS = 15
DOWNLOAD_TIMEOUT_S = 15
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
# Matches logo, icon and similar as separate words of the URL, so that e.g. "silicone" does not match
EXCLUDED_URL_PATTERN = re.compile(
    r"(^|[/_.\-])(logo|icon|favicon|avatar|gravatar|emoji|sprite|banner|pixel)s?([/_.\-]|$)|\.(svg|gif)(\?|$)",
    re.IGNORECASE,
)

ByteFetcher = Callable[[str, Optional[str]], bytes]


def fetch_image_bytes(url: str, referer: Optional[str] = None) -> bytes:
    """Downloads an image.

    Args:
        url: URL of the image.
        referer: URL of the post; some image hosts refuse requests without it.

    Returns:
        bytes: The file content.

    Raises:
        requests.RequestException: For network errors and error status codes.
        ValueError: If the file is larger than MAX_DOWNLOAD_BYTES.
    """
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    with requests.get(url, headers=headers, timeout=DOWNLOAD_TIMEOUT_S, stream=True) as response:
        response.raise_for_status()
        chunks = []
        size = 0
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"{url} is larger than {MAX_DOWNLOAD_BYTES} bytes")
            chunks.append(chunk)
    return b"".join(chunks)


def open_image(data: bytes) -> Image.Image:
    """Decodes an image, applies its EXIF orientation and converts it to RGB.

    Transparent areas become white.

    Args:
        data: The file content.

    Returns:
        Image.Image: The upright RGB image.

    Raises:
        OSError: If the data is not a readable image.
    """
    with Image.open(io.BytesIO(data)) as raw:
        raw.load()
        image = ImageOps.exif_transpose(raw)
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[3])
        return background
    return image if image.mode == "RGB" else image.convert("RGB")


def to_jpeg(image: Image.Image) -> bytes:
    """Resizes an image to at most IMAGE_MAX_SIDE_PX and encodes it as JPEG for the model.

    Args:
        image: An RGB image.

    Returns:
        bytes: The JPEG data.
    """
    resized = image.copy()
    resized.thumbnail((IMAGE_MAX_SIDE_PX, IMAGE_MAX_SIDE_PX), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    resized.save(buffer, format="JPEG", quality=IMAGE_JPEG_QUALITY)
    return buffer.getvalue()


def dhash(image: Image.Image) -> str:
    """Computes the 64-bit difference hash of an image.

    The image is reduced to 9x8 gray pixels; each bit says whether a pixel is
    brighter than its right neighbour. Resized or recompressed copies of an
    image get the same or a close hash.

    Args:
        image: The image.

    Returns:
        str: 16 hexadecimal digits.
    """
    pixels = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS).tobytes()
    value = 0
    for row in range(8):
        for column in range(8):
            value = (value << 1) | int(pixels[row * 9 + column] > pixels[row * 9 + column + 1])
    return f"{value:016x}"


def hamming(first: str, second: str) -> int:
    """Returns the number of differing bits of two hashes in hexadecimal form."""
    return (int(first, 16) ^ int(second, 16)).bit_count()


def download_and_resize_image(url: str) -> Optional[bytes]:
    """Downloads an image and returns it as JPEG for the model.

    Args:
        url: URL of the image.

    Returns:
        Optional[bytes]: The resized JPEG, or None if the download or decoding fails.
    """
    try:
        return to_jpeg(open_image(fetch_image_bytes(url)))
    except (requests.RequestException, OSError, ValueError, Image.DecompressionBombError) as e:
        logger.error("Failed to download or resize image %s: %s", url, e)
        return None


@dataclass(frozen=True)
class PreparedImage:
    """An image selected for the model.

    Attributes:
        candidate (ImageCandidate): The image candidate from the post.
        jpeg (bytes): The resized JPEG.
        hash (str): dHash of the image.
        width (int): Width of the downloaded image in pixels.
        height (int): Height of the downloaded image in pixels.
    """
    candidate: ImageCandidate
    jpeg: bytes
    hash: str
    width: int
    height: int


@dataclass
class ImageSelection:
    """Result of the image selection of one post.

    Attributes:
        images (list[PreparedImage]): Selected images in candidate order.
        rejected (Counter): Number of skipped candidates per reason.
    """
    images: list[PreparedImage] = field(default_factory=list)
    rejected: Counter = field(default_factory=Counter)


def select_images(
    candidates: Sequence[ImageCandidate],
    is_seen: Callable[[str], Optional[str]],
    max_images: int,
    referer: Optional[str] = None,
    fetch: ByteFetcher = fetch_image_bytes,
) -> ImageSelection:
    """Downloads candidates in order and keeps those that pass all filters.

    Args:
        candidates: Image candidates of the post, in priority order.
        is_seen: Returns the hash of an already analyzed image that matches a
            hash, or None (``PipelineState.find_similar_image``).
        max_images: Maximum number of selected images.
        referer: URL of the post, sent with the downloads.
        fetch: Function that downloads image bytes.

    Returns:
        ImageSelection: Selected images and the counts of skipped candidates.
    """
    selection = ImageSelection()
    downloads = 0
    for position, candidate in enumerate(candidates):
        if len(selection.images) >= max_images:
            selection.rejected["over limit"] += len(candidates) - position
            break
        if EXCLUDED_URL_PATTERN.search(candidate.url):
            selection.rejected["excluded url"] += 1
            continue
        if downloads >= MAX_CANDIDATE_DOWNLOADS:
            selection.rejected["download limit"] += 1
            continue
        downloads += 1
        try:
            image = open_image(fetch(candidate.url, referer))
        except (requests.RequestException, OSError, ValueError, Image.DecompressionBombError) as e:
            logger.info("Image %s skipped: %s", candidate.url, e)
            selection.rejected["download failed"] += 1
            continue
        width, height = image.size
        if min(width, height) < MIN_SIDE_PX:
            selection.rejected["too small"] += 1
            continue
        if not MIN_ASPECT_RATIO <= width / height <= MAX_ASPECT_RATIO:
            selection.rejected["aspect ratio"] += 1
            continue
        image_hash = dhash(image)
        if any(hamming(image_hash, prepared.hash) <= DHASH_MAX_DISTANCE for prepared in selection.images):
            selection.rejected["duplicate in post"] += 1
            continue
        if is_seen(image_hash) is not None:
            selection.rejected["seen before"] += 1
            continue
        selection.images.append(PreparedImage(candidate, to_jpeg(image), image_hash, width, height))
    return selection
