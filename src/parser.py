import os
import requests
from datetime import datetime
from typing import Optional
from crawl4ai import AsyncWebCrawler
from google import genai
from schema import FashionRecord
from tenacity import retry, wait_exponential, stop_after_attempt

@retry(wait=wait_exponential(multiplier=1, min=4, max=15), stop=stop_after_attempt(5))
def _generate_with_retry(client, model, contents, config):
    return client.models.generate_content(model=model, contents=contents, config=config)

def download_image_as_bytes(url: str) -> Optional[bytes]:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.content
    except Exception as e:
        print(f"[Error] Failed to download image {url}: {e}")
        return None

def parse_image_and_context(image_url: str, text_context: str, source_url: str) -> Optional[dict]:
    """Sends image and contextual metadata to Google Gemini for structured taxonomy mapping."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[Warning] GEMINI_API_KEY not found.")
        return None
        
    client = genai.Client(api_key=api_key)
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    is_trendsetter = any(word in text_context.lower() for word in ["runway", "celebrity", "red carpet", "model"])
    region = "EU" if any(word in text_context.lower() for word in ["paris", "milan", "berlin", "london", "eu"]) else "US"

    try:
        image_bytes = download_image_as_bytes(image_url)
        if not image_bytes:
            return None
            
        part = genai.types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

        prompt = f"Context: {text_context}. Enforce current date as {current_date}. Categorize the outfit and hair elements from the image strictly matching the provided schema rules."

        response = _generate_with_retry(
            client=client,
            model='gemini-3.5-flash',
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
    except Exception as e:
        print(f"[Error] Vision processing failed for {image_url}: {e}")
        return None

async def scrape_and_process_url(url: str) -> list[dict]:
    """Asynchronously executes crawls and orchestrates vision tasks per target."""
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
            parsed_record = parse_image_and_context(img_url, context_snippet, url)
            if parsed_record:
                parsed_results.append(parsed_record)
                
    return parsed_results
