"""
Defines the Pydantic schemas for the Fashion Analytics dataset.

Schema 2.0 separates the models that Gemini fills (``PostExtraction`` and its
parts) from the records written to disk (``OutfitRecord``). Vocabularies are
``Literal`` aliases, because google-genai drops the field descriptions of
``Enum`` classes when it builds the response schema. All fields of the models
sent to Gemini are required; absence is expressed with "Not visible",
"Not applicable" or null.
"""
import types
import typing
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = "2.0"

GARMENT_TYPES_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "Top": ("T-shirt", "Tank top/Camisole", "Blouse", "Button-up shirt", "Polo shirt", "Sweater",
            "Cardigan", "Sweatshirt/Hoodie", "Bodysuit", "Tunic"),
    "Bottom": ("Jeans", "Trousers", "Leggings", "Shorts", "Skirt", "Joggers/Sweatpants"),
    "Dress/One-piece": ("Dress", "Jumpsuit", "Romper/Playsuit", "Overalls"),
    "Outerwear": ("Blazer", "Coat", "Trench coat", "Denim jacket", "Leather jacket", "Bomber jacket",
                  "Puffer/Quilted jacket", "Shacket/Overshirt", "Vest/Gilet", "Cape/Poncho", "Other jacket"),
    "Footwear": ("Sneakers", "Ankle boots", "Knee-high boots", "Loafers", "Ballet flats", "Sandals",
                 "Heels/Pumps", "Mules/Slides", "Clogs", "Espadrilles", "Oxfords/Brogues"),
    "Bag": ("Tote", "Shoulder bag", "Crossbody bag", "Clutch", "Backpack", "Belt bag", "Top-handle bag"),
    "Headwear": ("Baseball cap", "Beanie", "Wide-brim hat", "Bucket hat", "Beret"),
    "Eyewear": ("Sunglasses", "Glasses"),
    "Jewelry": ("Necklace", "Earrings", "Bracelet", "Ring", "Watch", "Brooch"),
    "Belt": ("Belt",),
    "Scarf/Neckwear": ("Scarf", "Bandana", "Tie"),
    "Hair accessory": ("Headband", "Hair clip", "Hair bow", "Scrunchie"),
    "Hosiery": ("Tights", "Socks"),
    "Other": ("Other",),
}
GARMENT_CATEGORY_BY_TYPE: dict[str, str] = {
    garment_type: category
    for category, garment_types in GARMENT_TYPES_BY_CATEGORY.items()
    for garment_type in garment_types
}
# Categories whose colors describe the outfit, as opposed to accessories
CLOTHING_CATEGORIES = ("Top", "Bottom", "Dress/One-piece", "Outerwear", "Footwear")

# Literal[tuple(...)] builds the alias from the table above, so both cannot diverge
GarmentCategory = Literal[tuple(GARMENT_TYPES_BY_CATEGORY)]
GarmentType = Literal[tuple(GARMENT_CATEGORY_BY_TYPE)]

BaseColor = Literal["Black", "White", "Grey", "Beige/Cream", "Brown", "Camel/Tan", "Navy", "Blue",
                    "Light blue", "Denim blue", "Green", "Olive", "Red", "Burgundy", "Pink", "Purple",
                    "Yellow", "Orange", "Gold", "Silver", "Multicolor"]
SecondaryColor = Literal[BaseColor, "Not applicable"]
Pattern = Literal["Solid", "Striped", "Plaid/Check", "Floral", "Polka dot", "Animal print", "Abstract/Graphic",
                  "Geometric", "Paisley", "Camouflage", "Logo/Text", "Other", "Not applicable"]
Material = Literal["Denim", "Cotton", "Linen", "Knit/Wool", "Cashmere", "Silk/Satin", "Leather", "Suede",
                   "Faux fur/Shearling", "Technical/Synthetic", "Lace", "Sequins/Metallic", "Tweed/Boucle",
                   "Corduroy", "Velvet", "Straw/Raffia", "Canvas", "Metal", "Unclear", "Not applicable"]
Fit = Literal["Oversized", "Relaxed", "Regular", "Fitted", "Not applicable"]
Length = Literal["Cropped", "Hip", "Tunic-length", "Mini", "Knee", "Midi", "Maxi", "Ankle", "Full-length",
                 "Not visible", "Not applicable"]
Neckline = Literal["Crew", "Scoop", "V-neck", "Collared", "Turtleneck/Mock neck", "Boat", "Square",
                   "Off-the-shoulder", "Halter", "Asymmetric", "Not visible", "Not applicable"]
Rise = Literal["High-rise", "Mid-rise", "Low-rise", "Not visible", "Not applicable"]
LegShape = Literal["Skinny", "Straight", "Wide", "Flare/Bootcut", "Barrel", "Tapered", "Not visible",
                   "Not applicable"]
Finish = Literal["Matte", "Glossy/Patent", "Metallic", "Sheer", "Not applicable"]
DesignDetail = Literal["Ruffles", "Pleats", "Fringe", "Embroidery", "Sequins", "Cut-outs", "Buttons", "Zipper",
                       "Chains", "Studs", "Buckle", "Belted", "Pockets", "Bow", "Puff sleeves", "Slit"]

Framing = Literal["Full body", "Upper body", "Lower body", "Detail"]
PhotoContext = Literal["Blogger or private person", "Brand campaign or catalog", "Editorial or street style",
                       "Unclear"]
Gender = Literal["Female", "Male", "Unidentifiable"]
AgeGroup = Literal["Child", "Teen", "Young Adult", "Adult", "Senior", "Unidentifiable"]
StyleCategory = Literal["Casual", "Smart casual", "Business/Office", "Formal/Evening", "Minimalist",
                        "Classic/Preppy", "Bohemian", "Romantic/Feminine", "Streetwear", "Athleisure/Sporty",
                        "Edgy/Rock", "Vintage/Retro", "Eclectic/Maximalist", "Outdoor/Utility"]
AestheticTag = Literal["Quiet luxury/Old money", "Coastal", "Cottagecore", "Y2K", "Gorpcore", "Western", "Grunge",
                       "Balletcore", "Scandi", "Parisian chic", "Dark academia", "Normcore", "Mob wife", "Tomboy",
                       "Coquette", "Clean girl"]
OverallFit = Literal["Oversized", "Fitted/Tight", "Regular/Tailored", "Mixed", "Unclear"]
Silhouette = Literal["Oversized", "Fitted/Bodycon", "A-line", "Boxy", "Hourglass", "Draped", "Straight/Column",
                     "Unclear"]
ColorPaletteType = Literal["Monochrome", "Pastel", "Earth Tones", "Neon", "High Contrast", "Neutral", "Unclear"]
ColorContrastStrategy = Literal["Monochrome", "Color-blocking", "Neutral with a pop of color", "Tonal/Gradient",
                                "Clashing/Maximalist", "All neutrals", "Unclear"]
Occasion = Literal["Everyday casual", "Office/Professional", "Night out/Party", "Formal/Event",
                   "Activewear/Athleisure", "Vacation/Resort", "Unclear"]
Setting = Literal["Urban/Street", "Nature", "Indoors/Studio", "Event/Red Carpet", "Beach", "Unclear"]
Season = Literal["Spring", "Summer", "Autumn", "Winter", "Unclear"]
Weather = Literal["Sunny", "Overcast", "Rain", "Snow", "Not visible"]
HairLength = Literal["Short", "Chin-length", "Shoulder-length", "Long", "Not visible"]
HairTexture = Literal["Straight", "Wavy", "Curly", "Coily", "Not visible"]
HairStyleType = Literal["Down", "Ponytail", "Bun/Updo", "Braid", "Half-up", "Slicked back", "Covered", "Not visible"]
HairColor = Literal["Black", "Dark brown", "Light brown", "Blonde", "Red/Auburn", "Grey/White", "Fashion color",
                    "Not visible"]
HairParting = Literal["Middle part", "Side part", "Deep side part", "No part/Swept back", "Bangs/Fringe",
                      "Not visible"]
HairFinish = Literal["Sleek/Smooth", "Textured/Messy", "Voluminous/Blowout", "Wet look", "Natural", "Not visible"]
MakeupStyle = Literal["Natural/Minimal", "Bold lips", "Bold eyes", "Full glam", "Not visible"]
PriceSegment = Literal["Fast Fashion", "Mid-range", "Luxury", "Unknown"]
RejectionReason = Literal["Product/flat lay", "No person", "Detail crop", "Collage", "Other"]
CountryBasis = Literal["model", "domain", "feed_language", "unknown"]


class Garment(BaseModel):
    """One garment, shoe or accessory of an outfit, as filled by the model."""
    garment_type: GarmentType = Field(description="Closest type from the list. Use 'Other' only if no listed type fits.")
    description: Optional[str] = Field(description="Short specific description, e.g. 'Chambray button-up shirt' or 'Woven leather tote'. Null if the type says everything.")
    primary_color: BaseColor = Field(description="Color that covers the largest area of the item.")
    secondary_color: SecondaryColor = Field(description="Second clearly visible color of the item, or 'Not applicable' for items in one color.")
    pattern: Pattern = Field(description="Pattern of the item. 'Solid' for plain items, 'Not applicable' for items without a surface pattern, such as metal jewelry.")
    material: Material = Field(description="Visually inferred main material. 'Unclear' if it cannot be judged from the images.")
    fit: Fit = Field(description="How the item fits the body. 'Not applicable' for shoes, bags, jewelry and other accessories.")
    length: Length = Field(description="Length of the item: Cropped, Hip or Tunic-length for tops and outerwear; Mini to Maxi for skirts and dresses; Ankle or Full-length for trousers. 'Not visible' if cut off by the photo, 'Not applicable' for other items.")
    neckline: Neckline = Field(description="Neckline of tops, dresses and one-pieces. 'Not visible' if covered, 'Not applicable' for other items.")
    rise: Rise = Field(description="Rise of trousers, jeans, shorts and skirts. 'Not visible' if covered by a top, 'Not applicable' for other items.")
    leg_shape: LegShape = Field(description="Leg shape of trousers, jeans, leggings and shorts. 'Not visible' if cut off, 'Not applicable' for other items.")
    finish: Finish = Field(description="Optical surface quality of the material. 'Not applicable' if it does not apply.")
    design_details: list[DesignDetail] = Field(description="Visible design details of the item. Empty list if none.")


class OutfitAttributes(BaseModel):
    """Attributes of one person's outfit that the model fills."""
    framing: Framing = Field(description="Widest view of the person among the images of this outfit.")
    photo_context: PhotoContext = Field(description="Who is shown: the blogger or another private person, models in a brand campaign or catalog image, or an editorial or street-style photo.")
    gender: Gender = Field(description="Perceived gender presentation of the person.")
    age_group: AgeGroup = Field(description="Visually estimated age bracket of the person.")
    style_category: StyleCategory = Field(description="Primary style of the outfit.")
    style_detail: Optional[str] = Field(description="Refinement of the style in a few words, e.g. 'Relaxed weekend minimalism'. Null if not needed.")
    aesthetic_tags: list[AestheticTag] = Field(description="Named aesthetics or micro-trends that the outfit clearly represents. Empty list if none.")
    aesthetic_other: Optional[str] = Field(description="A clearly represented aesthetic that is not in the list, in one to three words. Null if none.")
    garments: list[Garment] = Field(description="Every visible garment, shoe and accessory of this outfit, each listed once.")
    focal_garment_index: Optional[int] = Field(description="Position (starting at 1) in the garments list of the piece that draws the eye most. Null if no piece stands out.")
    layering_complexity: int = Field(description="Number of visible clothing layers on the upper body, from 1 (single layer) to 5 (heavy layering).")
    overall_fit: OverallFit = Field(description="Overall fit of the outfit.")
    silhouette: Silhouette = Field(description="Overall outline of the outfit.")
    color_palette_type: ColorPaletteType = Field(description="Overall color character of the outfit.")
    color_contrast_strategy: ColorContrastStrategy = Field(description="How the colors of the outfit are combined.")
    occasion: Occasion = Field(description="Most likely occasion the outfit is meant for. 'Unclear' if the images and text give no indication.")
    setting: Setting = Field(description="Setting or background of the photos.")
    season: Season = Field(description="Season suggested by the clothing and the surroundings. 'Unclear' if they give no indication.")
    weather: Weather = Field(description="Weather visible in the photos. 'Not visible' for indoor photos and whenever sky or conditions cannot be seen.")
    hair_length: HairLength = Field(description="Hair length of the person.")
    hair_texture: HairTexture = Field(description="Hair texture of the person.")
    hair_style_type: HairStyleType = Field(description="How the hair is worn.")
    hair_color: HairColor = Field(description="Hair color of the person.")
    hair_parting: HairParting = Field(description="How the hair is parted.")
    hair_finish: HairFinish = Field(description="Styling finish of the hair.")
    makeup_style: MakeupStyle = Field(description="Makeup style of the person.")
    brand_mentions: list[str] = Field(description="Brands of this outfit that are named in the post title, text, tags, image captions or link texts. Do not infer brands from appearance. Empty list if none.")
    price_segment: PriceSegment = Field(description="Price segment suggested by the named brands or the text. 'Unknown' without such evidence.")

    @field_validator("layering_complexity")
    @classmethod
    def _clamp_layering(cls, value: int) -> int:
        """Clamps the layering scale to 1-5, so that one value out of range does not invalidate the post."""
        return max(1, min(5, value))


class OutfitExtraction(OutfitAttributes):
    """One person's outfit as returned by the model, with the images that show it."""
    image_indexes: list[int] = Field(description="Numbers (starting at 1) of all images that show this person in this outfit.")


class RejectedImage(BaseModel):
    """An image that shows no outfit."""
    index: int = Field(description="Number of the image, starting at 1.")
    reason: RejectionReason = Field(description="Why the image shows no outfit.")


class PostExtraction(BaseModel):
    """The model response for all images of one post."""
    visual_analysis: str = Field(description="Describe each image in one or two sentences: who is shown, how the person is framed, and the main clothing. Then state in one or two sentences which images show the same person in the same outfit, and which images show no outfit.")
    outfits: list[OutfitExtraction] = Field(description="One entry per person and outfit. All images of the same person in the same clothes belong to one entry.")
    rejected_images: list[RejectedImage] = Field(description="Images that show no person wearing an outfit, with the reason.")


class GarmentRecord(Garment):
    """A garment as stored in the dataset, with its category."""
    category: GarmentCategory = Field(description="Category of the item, derived from garment_type.")


class ImageRef(BaseModel):
    """Reference to an analyzed image. The image itself is not stored."""
    hash: str = Field(description="Perceptual hash (dHash) of the image as 16 hexadecimal digits.")
    width: int = Field(description="Width of the downloaded image in pixels.")
    height: int = Field(description="Height of the downloaded image in pixels.")


class OutfitRecord(OutfitAttributes):
    """One outfit record of schema 2.0 as written to the dataset."""
    garments: list[GarmentRecord] = Field(description="Every visible garment, shoe and accessory of this outfit.")
    record_id: str = Field(description="Stable id derived from the post URL, the image hashes and the position of the outfit in the post.")
    schema_version: str = Field(description="Version of the record schema.")
    model: str = Field(description="Gemini model that produced the record.")
    extraction_hash: str = Field(description="Hash of the prompt, response schema, model settings and image preprocessing. Records with the same hash were extracted under the same conditions.")
    date_scraped: str = Field(description="UTC date of the extraction (YYYY-MM-DD).")
    published_date: Optional[str] = Field(description="Publish date of the post from its feed (YYYY-MM-DD).")
    source_id: str = Field(description="Domain of the blog, without www.")
    source_country: Optional[str] = Field(description="ISO 3166-1 alpha-2 country of the blog. Null if unknown.")
    source_country_basis: CountryBasis = Field(description="How the country was determined: model (Gemini with web search), domain (country-code top-level domain), feed_language (region of the RSS language tag) or unknown.")
    source_region: str = Field(description="Region derived from source_country.")
    source_language: Optional[str] = Field(description="Language of the blog from its feed (ISO 639-1).")
    post_url: str = Field(description="URL of the post.")
    canonical_url: Optional[str] = Field(description="Canonical URL declared by the post page.")
    post_title: Optional[str] = Field(description="Title of the post.")
    post_tags: list[str] = Field(description="Categories and tags of the post from its feed.")
    images: list[ImageRef] = Field(description="Images of the post that show this outfit.")
    shopping_link_count: int = Field(description="Number of links to shops or affiliate networks in the post.")
    outfit_colors: list[BaseColor] = Field(description="Primary colors of the clothing and footwear, in the order of the garments list.")


# Explicit map, so that for example GB counts as Europe without implying EU membership
REGION_BY_COUNTRY: dict[str, str] = {
    **dict.fromkeys(("US", "CA", "MX"), "North America"),
    **dict.fromkeys(("AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GB", "GR", "HR",
                     "HU", "IE", "IS", "IT", "LT", "LU", "LV", "MT", "NL", "NO", "PL", "PT", "RO", "SE", "SI",
                     "SK"), "Europe"),
    **dict.fromkeys(("AU", "NZ"), "Oceania"),
    **dict.fromkeys(("CN", "HK", "ID", "IN", "JP", "KR", "MY", "PH", "SG", "TH", "TW", "VN"), "Asia"),
    **dict.fromkeys(("AR", "BR", "CL", "CO", "PE"), "South America"),
    **dict.fromkeys(("EG", "KE", "MA", "NG", "ZA"), "Africa"),
    **dict.fromkeys(("AE", "IL", "KW", "QA", "SA"), "Middle East"),
}


def region_for_country(country: Optional[str]) -> str:
    """Returns the region of an ISO 3166-1 alpha-2 country code.

    Args:
        country: Country code, or None if the country is unknown.

    Returns:
        str: The region, "Other" for countries outside the map, "Unknown" without a country.
    """
    if not country:
        return "Unknown"
    return REGION_BY_COUNTRY.get(country.upper(), "Other")


SCHEMA_MODELS: tuple[type[BaseModel], ...] = (
    Garment, OutfitExtraction, RejectedImage, PostExtraction, GarmentRecord, ImageRef, OutfitRecord,
)


def _describe_type(annotation: object) -> str:
    """Returns a text form of a type annotation that is the same on all supported Python versions.

    Args:
        annotation: A type annotation of a model field.

    Returns:
        str: For example ``list[Garment]`` or ``Union[NoneType,str]``.
    """
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is Literal:
        return "Literal[" + ",".join(repr(arg) for arg in args) + "]"
    if origin is typing.Union or origin is types.UnionType:
        return "Union[" + ",".join(sorted(_describe_type(arg) for arg in args)) + "]"
    if origin is list:
        return "list[" + _describe_type(args[0]) + "]"
    if isinstance(annotation, type):
        return annotation.__name__
    return repr(annotation)


def describe_models(include_descriptions: bool) -> str:
    """Returns a canonical text form of the schema 2.0 models.

    The text lists every field with its type and whether it is required. It is
    used for the extraction hash (with descriptions, because they are part of
    the prompt) and for the schema snapshot test (without descriptions).

    Args:
        include_descriptions: Whether to include the field descriptions.

    Returns:
        str: One line per model and per field.
    """
    lines = []
    for model in SCHEMA_MODELS:
        lines.append(model.__name__)
        for name, field in model.model_fields.items():
            line = f"  {name}: {_describe_type(field.annotation)} required={field.is_required()}"
            if include_descriptions:
                line += f" description={field.description!r}"
            lines.append(line)
    return "\n".join(lines)
