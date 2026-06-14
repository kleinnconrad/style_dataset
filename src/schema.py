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
