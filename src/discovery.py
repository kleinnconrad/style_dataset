"""
Discovers independent fashion blogs dynamically using Gemini Search Grounding.
"""
import os
import json
import logging
from typing import List, Optional
from google import genai
from google.genai import types
from google.genai.errors import APIError
from tenacity import retry, wait_exponential_jitter, stop_after_attempt, retry_if_exception_type

logger = logging.getLogger(__name__)

@retry(
    wait=wait_exponential_jitter(initial=10, max=60),
    stop=stop_after_attempt(7),
    retry=retry_if_exception_type(APIError)
)
def _generate_with_retry(client: genai.Client, model: str, contents: str, config: types.GenerateContentConfig):
    """Executes the generate_content API call with exponential backoff retries."""
    return client.models.generate_content(model=model, contents=contents, config=config)

def discover_targets(ignore_urls: Optional[List[str]] = None) -> List[str]:
    """
    Finds independent fashion platforms using Gemini with Google Search.
    
    Args:
        ignore_urls (Optional[List[str]]): List of URLs to exclude from results.
        
    Returns:
        List[str]: A list of discovered target URLs.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not found. Skipping dynamic discovery.")
        return []
        
    client = genai.Client(api_key=api_key)
    
    prompt = (
        "Search the web for small, independent fashion blogs or 'outfit of the day' "
        "forums that do not have heavy bot protection. "
        "Return EXACTLY a valid JSON array containing 5 URL strings, and no other markdown or text. "
        "Example: [\"https://example.com/blog\", \"https://example2.com/forum\"]"
    )
    
    if ignore_urls:
        prompt += f"\nDO NOT return any of these previously scraped URLs: {', '.join(ignore_urls)}"
    
    try:
        response = _generate_with_retry(
            client=client,
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[{'google_search': {}}],
                temperature=0.7
            )
        )
        
        # Clean potential markdown from response
        text = response.text.strip()
        if text.startswith('```json'):
            text = text[7:-3].strip()
        elif text.startswith('```'):
            text = text[3:-3].strip()
            
        urls = json.loads(text)
        if isinstance(urls, list):
            return [str(url) for url in urls if url.startswith('http')]
        return []
    except json.JSONDecodeError as e:
        logger.error("Failed to parse JSON from Gemini response: %s", e)
        return []
    except APIError as e:
        logger.error("Gemini API error during discovery: %s", e)
        return []
    except Exception as e:
        logger.error("Unexpected error during discovery: %s", e)
        return []
