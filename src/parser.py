"""
Extracts images and parses them into structured records using Google Gemini.
"""
import os
import logging
import requests
from datetime import datetime
from typing import List, Dict, Optional, Any
from crawl4ai import AsyncWebCrawler
from google import genai
from google.genai import types
from google.genai.errors import APIError
import asyncio
from pydantic import ValidationError
from schema import FashionRecord
from tenacity import retry, wait_exponential_jitter, stop_after_attempt, retry_if_exception_type

logger = logging.getLogger(__name__)

@retry(
    wait=wait_exponential_jitter(initial=10, max=60),
    stop=stop_after_attempt(7),
    retry=retry_if_exception_type(APIError)
)
async def _generate_with_retry(client: genai.Client, model: str, contents: list, config: types.GenerateContentConfig):
    """Executes the generate_content API call with exponential backoff retries."""
    return await client.aio.models.generate_content(model=model, contents=contents, config=config)

def download_and_resize_image(url: str, max_size: tuple = (1024, 1024)) -> Optional[bytes]:
    """
    Downloads an image from a URL, resizes it using Pillow, and returns it as a byte array.
    
    Args:
        url (str): The URL of the image to download.
        max_size (tuple): Max width and height for resizing.
        
    Returns:
        Optional[bytes]: The resized image content as JPEG bytes, or None if it fails.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(response.content))
        
        # Convert RGBA to RGB for JPEG compatibility
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
            
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG', quality=85)
        return img_byte_arr.getvalue()
    except Exception as e:
        logger.error("Failed to download or resize image %s: %s", url, e)
        return None

async def parse_image_and_context(image_url: str, text_context: str, source_url: str) -> Optional[Dict[str, Any]]:
    """
    Sends image and contextual metadata to Google Gemini for structured taxonomy mapping.
    
    Args:
        image_url (str): URL of the target image.
        text_context (str): Markdown text snippet from the page.
        source_url (str): Origin URL of the webpage.
        
    Returns:
        Optional[Dict[str, Any]]: A dictionary conforming to FashionRecord, or None if invalid/failed.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not found.")
        return None
        
    client = genai.Client(api_key=api_key)
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    is_trendsetter = any(word in text_context.lower() for word in ["runway", "celebrity", "red carpet", "model"])
    region = "EU" if any(word in text_context.lower() for word in ["paris", "milan", "berlin", "london", "eu"]) else "US"

    try:
        image_bytes = await asyncio.to_thread(download_and_resize_image, image_url)
        if not image_bytes:
            return None
            
        part = genai.types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

        prompt = f"Context: {text_context}. Enforce current date as {current_date}. Categorize the outfit and hair elements from the image strictly matching the provided schema rules."

        response = await _generate_with_retry(
            client=client,
            model='gemini-2.5-flash',
            contents=[part, prompt],
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=FashionRecord,
                system_instruction="You are a professional fashion ontology compiler."
            )
        )
        
        record = FashionRecord.model_validate_json(response.text)

        if not record.is_valid_outfit:
            return None

        record.date_scraped = current_date
        record.source_url = source_url
        record.is_trendsetter = is_trendsetter
        record.region = region
        
        # GDPR Compliance
        if record.is_trendsetter:
            record.image_url = image_url
        else:
            record.image_url = None
            
        return record.model_dump(exclude={'is_valid_outfit'})
    except APIError as e:
        logger.error("Gemini API error during vision processing for %s: %s", image_url, e)
        return None
    except ValidationError as e:
        logger.error("Schema validation failed for %s: %s", image_url, e)
        return None
    except Exception as e:
        logger.error("Unexpected error processing %s: %s", image_url, e)
        return None

async def scrape_and_process_url(url: str) -> List[Dict[str, Any]]:
    """
    Asynchronously executes crawls and orchestrates vision tasks per target.
    
    Args:
        url (str): Target webpage URL to scrape.
        
    Returns:
        List[Dict[str, Any]]: A list of extracted fashion records from the page.
    """
    parsed_results = []
    
    async with AsyncWebCrawler(verbose=False) as crawler:
        result = await crawler.arun(url=url, bypass_cache=True, magic=True)
        
        if not result.success or not result.media:
            return []
            
        valid_images = [img["src"] for img in result.media.get("images", []) if "src" in img and "logo" not in img.get("src", "").lower()][:3]
        context_snippet = result.markdown[:1000] if result.markdown else ""
        
        for img_url in valid_images:
            if not img_url.startswith("http"):
                continue
            parsed_record = await parse_image_and_context(img_url, context_snippet, url)
            if parsed_record:
                parsed_results.append(parsed_record)
            
            # Explicit rate limiting to prevent overloading the API
            await asyncio.sleep(5)
                
    return parsed_results
