"""
Defines the Pydantic schemas for the Fashion Analytics dataset.
"""
from pydantic import BaseModel, Field
from typing import Literal, Optional

class FashionRecord(BaseModel):
    """
    Represents a single extracted fashion record from a target website.
    
    Attributes:
        is_valid_outfit (bool): True if the image clearly shows an outfit.
        date_scraped (str): ISO format date YYYY-MM-DD.
        source_url (str): The origin URL of the image context.
        clothing_style (str): The primary fashion style.
        hairstyle (str): The primary hairstyle of the subject.
        gender (Literal["Male", "Female", "Unidentifiable"]): Perceived gender of the subject.
        primary_colors (list[str]): List of dominant colors in the outfit.
        is_trendsetter (bool): True if celebrity/model, False if regular person.
        region (Literal["EU", "US"]): Geographic region identified from context.
        confidence_score (float): Model confidence score (0.0 to 1.0).
        image_url (Optional[str]): Image URL of the subject (GDPR compliant).
    """
    is_valid_outfit: bool = Field(description="MUST be True if the image clearly shows a person wearing clothing/outfit. MUST be False if the image is a flat lay, jewelry only, a product shot, a landscape, or does not contain a person.")
    date_scraped: str = Field(description="ISO format date YYYY-MM-DD")
    source_url: str
    clothing_style: str = Field(description="The primary fashion style, e.g., Streetwear, Casual, Minimalist, Vintage, Formal, Gorpcore, etc. Be descriptive if it doesn't fit standard categories.")
    hairstyle: str = Field(description="The primary hairstyle of the subject, e.g., Long, Short, Updo, Ponytail, Buzzcut, Bald, etc. Be descriptive.")
    gender: Literal["Male", "Female", "Unidentifiable"] = Field(description="The perceived gender of the subject based on visual appearance.")
    primary_colors: list[str]
    is_trendsetter: bool = Field(description="True if celebrity/model/artist, False if regular person")
    region: Literal["EU", "US"]
    confidence_score: float = Field(ge=0.0, le=1.0)
    image_url: Optional[str] = Field(default=None, description="Image URL of the subject (Only stored for trendsetters, omitted for regular people to ensure GDPR compliance)")

    # Granular Garment & Outfit Details
    top_garment_type: Optional[str] = Field(default=None, description="The type of top being worn, e.g., Blazer, Graphic T-shirt, Chunky Knit Sweater.")
    bottom_garment_type: Optional[str] = Field(default=None, description="The type of bottom being worn, e.g., Wide-leg jeans, Pleated skirt, Cargo pants.")
    footwear_type: Optional[str] = Field(default=None, description="The type of shoes being worn, e.g., Sneakers, Boots, Loafers, Heels.")
    accessories: list[str] = Field(default_factory=list, description="List of visible accessories like bags, sunglasses, jewelry, or hats.")
    patterns: list[str] = Field(default_factory=list, description="Patterns visible on the clothing, e.g., Striped, Floral, Plaid, Solid.")
    fabric_textures: list[str] = Field(default_factory=list, description="Visually inferred materials, e.g., Denim, Leather, Silk, Ribbed Knit.")
    clothing_fit: Optional[Literal["Oversized", "Fitted/Tight", "Regular/Tailored", "Mixed"]] = Field(default=None, description="The overall fit of the clothing.")

    # Environmental & Contextual Data
    setting: Optional[Literal["Urban/Street", "Nature", "Indoors/Studio", "Event/Red Carpet", "Beach", "Unclear"]] = Field(default=None, description="The setting or background of the photo.")
    seasonality: Optional[Literal["Spring", "Summer", "Autumn", "Winter", "Unclear"]] = Field(default=None, description="The inferred season based on clothing layers and environment.")
    weather_conditions: Optional[str] = Field(default=None, description="Inferred weather, e.g., Sunny, Raining, Snowy, Overcast.")
    pose_or_activity: Optional[str] = Field(default=None, description="What the subject is doing, e.g., Walking confidently, Sitting, Posing.")

    # Subject Demographics & Presentation
    age_group: Optional[Literal["Child", "Teen", "Young Adult", "Adult", "Senior", "Unidentifiable"]] = Field(default=None, description="Visually estimated age bracket.")
    makeup_style: Optional[str] = Field(default=None, description="Subject's makeup style, e.g., Natural, Bold lips, Smokey eye, Not visible.")
    hair_color: Optional[str] = Field(default=None, description="Subject's hair color, e.g., Blonde, Brunette, Black, Pink, Red.")

    # Text-Derived Context
    brand_mentions: list[str] = Field(default_factory=list, description="Any fashion brands explicitly mentioned in the text context.")
    price_segment: Optional[Literal["Fast Fashion", "Mid-range", "Luxury", "Unknown"]] = Field(default=None, description="Inferred price segment based on text or brands.")
    sentiment_or_vibe: Optional[str] = Field(default=None, description="The aesthetic vibe described in the text, e.g., Effortless chic, Grunge.")

    # High-Level Aesthetics
    color_palette_type: Optional[Literal["Monochrome", "Pastel", "Earth Tones", "Neon", "High Contrast", "Neutral"]] = Field(default=None, description="The overall color theory of the outfit.")
    layering_complexity: Optional[int] = Field(default=None, description="Scale from 1 (simple t-shirt) to 5 (heavy layering).")
    focal_point: Optional[str] = Field(default=None, description="The standout piece that draws the eye the most.")
