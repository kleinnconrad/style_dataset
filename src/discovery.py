import os
import json
from google import genai
from google.genai import types
from tenacity import retry, wait_exponential, stop_after_attempt

@retry(wait=wait_exponential(multiplier=1, min=4, max=15), stop=stop_after_attempt(5))
def _generate_with_retry(client, model, contents, config):
    return client.models.generate_content(model=model, contents=contents, config=config)

def discover_targets(ignore_urls: list[str] = None) -> list[str]:
    """Finds independent fashion platforms using Gemini with Google Search."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[Warning] GEMINI_API_KEY not found. Skipping dynamic discovery.")
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
            model='gemini-3.5-flash',
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
    except Exception as e:
        print(f"[Error] Gemini discovery failed: {e}")
        return []
