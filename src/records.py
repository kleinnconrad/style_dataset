"""
Builds schema 2.0 outfit records from a cleaned extraction and the post metadata.

Outfits of minors are not stored: the dataset covers adult fashion, and
records about children are personal data without analytical purpose here.
"""
import hashlib
import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from schema import (
    CLOTHING_CATEGORIES,
    GARMENT_CATEGORY_BY_TYPE,
    SCHEMA_VERSION,
    GarmentRecord,
    ImageRef,
    OutfitAttributes,
    OutfitRecord,
    PostExtraction,
    region_for_country,
)
from urls import normalize_url

logger = logging.getLogger(__name__)

EXCLUDED_AGE_GROUPS = frozenset({"Child", "Teen"})


def is_excluded(outfit: OutfitAttributes) -> bool:
    """Returns True for outfits that are not stored, such as those of minors.

    Args:
        outfit: An outfit from the model output.

    Returns:
        bool: True if no record is built for the outfit.
    """
    return outfit.age_group in EXCLUDED_AGE_GROUPS


@dataclass(frozen=True)
class PostMetadata:
    """Metadata of a post that is copied into its records.

    Attributes:
        url (str): URL of the post.
        canonical_url (Optional[str]): Canonical URL declared by the page.
        title (Optional[str]): Title of the post.
        published_date (Optional[str]): Publish date (YYYY-MM-DD).
        tags (tuple[str, ...]): Categories and tags from the feed.
        shopping_link_count (int): Number of links to shops or affiliate networks.
    """
    url: str
    canonical_url: Optional[str]
    title: Optional[str]
    published_date: Optional[str]
    tags: tuple[str, ...]
    shopping_link_count: int


@dataclass(frozen=True)
class SourceMetadata:
    """Metadata of the blog that published a post.

    Attributes:
        source_id (str): Domain of the blog, without www.
        country (Optional[str]): ISO 3166-1 alpha-2 country, or None if unknown.
        country_basis (str): How the country was determined (see ``CountryBasis``).
        language (Optional[str]): ISO 639-1 language from the feed.
    """
    source_id: str
    country: Optional[str]
    country_basis: str
    language: Optional[str]


def make_record_id(post_url: str, image_hashes: Sequence[str], position: int) -> str:
    """Returns a stable id for an outfit record.

    Args:
        post_url: URL of the post; normalized before hashing.
        image_hashes: Hashes of the images that show the outfit.
        position: Position of the outfit in the post, starting at 1. Separates
            two people shown in the same images.

    Returns:
        str: 16 hexadecimal digits.
    """
    key = "|".join([normalize_url(post_url), ",".join(sorted(image_hashes)), str(position)])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _outfit_colors(garments: Sequence[GarmentRecord]) -> list[str]:
    """Returns the primary colors of the clothing and footwear, without duplicates.

    Args:
        garments: The garments of an outfit with their categories.

    Returns:
        list[str]: Colors in the order of the garments list.
    """
    colors = (garment.primary_color for garment in garments if garment.category in CLOTHING_CATEGORIES)
    return list(dict.fromkeys(colors))


def build_records(
    extraction: PostExtraction,
    images: Sequence[ImageRef],
    post: PostMetadata,
    source: SourceMetadata,
    *,
    model: str,
    extraction_hash: str,
    date_scraped: str,
) -> list[OutfitRecord]:
    """Turns the outfits of a cleaned extraction into dataset records.

    Args:
        extraction: Cleaned extraction; image numbers refer to ``images``.
        images: References of the images sent to the model, in request order.
        post: Metadata of the post.
        source: Metadata of the blog.
        model: Gemini model id.
        extraction_hash: Hash of the extraction setup (see ``extraction.extraction_hash``).
        date_scraped: UTC date of the run (YYYY-MM-DD).

    Returns:
        list[OutfitRecord]: One record per stored outfit; outfits of minors are skipped.
    """
    records = []
    for position, outfit in enumerate(extraction.outfits, start=1):
        if is_excluded(outfit):
            logger.info("Skipped outfit %d of %s: age group %s is not stored.", position, post.url, outfit.age_group)
            continue
        refs = [images[index - 1] for index in outfit.image_indexes]
        garments = [
            GarmentRecord(**garment.model_dump(), category=GARMENT_CATEGORY_BY_TYPE[garment.garment_type])
            for garment in outfit.garments
        ]
        records.append(OutfitRecord(
            **outfit.model_dump(exclude={"image_indexes", "garments"}),
            garments=garments,
            record_id=make_record_id(post.url, [ref.hash for ref in refs], position),
            schema_version=SCHEMA_VERSION,
            model=model,
            extraction_hash=extraction_hash,
            date_scraped=date_scraped,
            published_date=post.published_date,
            source_id=source.source_id,
            source_country=source.country,
            source_country_basis=source.country_basis,
            source_region=region_for_country(source.country),
            source_language=source.language,
            post_url=post.url,
            canonical_url=post.canonical_url,
            post_title=post.title,
            post_tags=list(post.tags),
            images=refs,
            shopping_link_count=post.shopping_link_count,
            outfit_colors=_outfit_colors(garments),
        ))
    return records
