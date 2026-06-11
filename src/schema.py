from pydantic import BaseModel, Field
from typing import Literal, Optional

class FashionRecord(BaseModel):
    date_scraped: str = Field(description="ISO format date YYYY-MM-DD")
    source_url: str
    clothing_style: Literal[
        "Streetwear", "Gorpcore", "Y2K", "Minimalist", 
        "Corporate", "Grunge", "Athleisure", "Unidentifiable"
    ]
    hairstyle: Literal[
        "Buzzcut", "Mullet", "Curtain Bangs", "French Bob", 
        "Long Waves", "Updo", "Unidentifiable"
    ]
    primary_colors: list[str]
    is_trendsetter: bool = Field(description="True if celebrity/model/artist, False if regular person")
    region: Literal["EU", "US"]
    confidence_score: float = Field(ge=0.0, le=1.0)
    image_url: Optional[str] = Field(default=None, description="Image URL of the subject (Only stored for trendsetters, omitted for regular people to ensure GDPR compliance)")
