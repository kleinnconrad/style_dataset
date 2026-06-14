from pydantic import BaseModel, Field
from typing import Literal, Optional

class FashionRecord(BaseModel):
    is_valid_outfit: bool = Field(description="MUST be True if the image clearly shows a person wearing clothing/outfit. MUST be False if the image is a flat lay, jewelry only, a product shot, a landscape, or does not contain a person.")
    date_scraped: str = Field(description="ISO format date YYYY-MM-DD")
    source_url: str
    clothing_style: str = Field(description="The primary fashion style, e.g., Streetwear, Casual, Minimalist, Vintage, Formal, Gorpcore, etc. Be descriptive if it doesn't fit standard categories.")
    hairstyle: str = Field(description="The primary hairstyle of the subject, e.g., Long, Short, Updo, Ponytail, Buzzcut, Bald, etc. Be descriptive.")
    primary_colors: list[str]
    is_trendsetter: bool = Field(description="True if celebrity/model/artist, False if regular person")
    region: Literal["EU", "US"]
    confidence_score: float = Field(ge=0.0, le=1.0)
    image_url: Optional[str] = Field(default=None, description="Image URL of the subject (Only stored for trendsetters, omitted for regular people to ensure GDPR compliance)")
