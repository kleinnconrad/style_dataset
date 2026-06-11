# Fashion Analytics Pipeline Architecture

This document specifies the decoupled, agentic architecture for an autonomous fashion analytics pipeline that runs daily via GitHub Actions and stores structured data directly in Google Drive.

---

## 1. Project Directory Structure

```text
fashion-analytics-scraper/
├── .github/
│   └── workflows/
│       └── daily_scraper.yml
├── src/
│   ├── __init__.py
│   ├── discovery.py
│   ├── gdrive.py
│   ├── parser.py
│   └── schema.py
├── main.py
├── README.md
└── requirements.txt
```

---

## 2. Configuration & Workflow Files

### `requirements.txt`
```text
crawl4ai>=0.3.0
pydantic>=2.0.0
google-genai>=1.0.0
google-api-python-client>=2.0.0
google-auth-httplib2>=0.2.0
google-auth-oauthlib>=1.2.0

requests
```

### `.github/workflows/daily_scraper.yml`
```yaml
name: Daily Fashion Analytics Pipeline

on:
  schedule:
    - cron: '0 0 * * *'  # Runs daily at 00:00 UTC
  workflow_dispatch:      # Allows manual execution via the GitHub UI

jobs:
  run-pipeline:
    runs-on: ubuntu-latest

    steps:
    - name: Checkout Repository
      uses: actions/checkout@v4

    - name: Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: '3.11'
        cache: 'pip'

    - name: Install Dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt

    - name: Initialize Crawl4AI
      run: |
        crawl4ai-setup

    - name: Execute Pipeline
      env:
        GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}

        GDRIVE_FOLDER_ID: ${{ secrets.GDRIVE_FOLDER_ID }}
        GDRIVE_SERVICE_ACCOUNT_JSON: ${{ secrets.GDRIVE_SERVICE_ACCOUNT_JSON }}
      run: |
        python main.py
```

---

## 3. Core Source Code

### `src/schema.py`
```python
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
```

### `src/discovery.py`
```python
import os
import json
from google import genai
from google.genai import types

def discover_targets() -> list[str]:
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
    
    try:
        response = client.models.generate_content(
            model='gemini-3.1-pro',
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
```

### `src/parser.py`
```python
import os
import requests
from datetime import datetime
from typing import Optional
from crawl4ai import AsyncWebCrawler
from google import genai
from src.schema import FashionRecord

def download_image_as_bytes(url: str) -> Optional[bytes]:
    try:
        response = requests.get(url, timeout=10)
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

        response = client.models.generate_content(
            model='gemini-3.1-pro',
            contents=[part, prompt],
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=FashionRecord,
                system_instruction="You are a professional fashion ontology compiler."
            )
        )
        
        record = FashionRecord.model_validate_json(response.text)

        record.date_scraped = current_date
        record.source_url = source_url
        record.is_trendsetter = is_trendsetter
        record.region = region
        
        # GDPR Compliance
        if record.is_trendsetter:
            record.image_url = image_url
        else:
            record.image_url = None
            
        return record.model_dump()
    except Exception as e:
        print(f"[Error] Vision processing failed for {image_url}: {e}")
        return None

async def scrape_and_process_url(url: str) -> list[dict]:
    """Asynchronously executes crawls and orchestrates vision tasks per target."""
    parsed_results = []
    
    async with AsyncWebCrawler(verbose=False) as crawler:
        result = await crawler.arun(url=url, bypass_cache=True)
        
        if not result.success or not result.media:
            return []
            
        valid_images = [img["src"] for img in result.media if img.get("type") == "image" and "logo" not in img.get("src", "").lower()][:3]
        context_snippet = result.markdown[:1000] if result.markdown else ""
        
        for img_url in valid_images:
            if not img_url.startswith("http"):
                continue
            parsed_record = parse_image_and_context(img_url, context_snippet, url)
            if parsed_record:
                parsed_results.append(parsed_record)
                
    return parsed_results
```

### `src/gdrive.py`
```python
import os
import json
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

def upload_to_drive(dataset: list[dict]):
    """Authenticates using an in-memory service account token string and pushes the dataset payload."""
    folder_id = os.getenv("GDRIVE_FOLDER_ID")
    sa_json_str = os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON")
    
    if not sa_json_str or not folder_id:
        print("[Warning] Google Drive operational variables missing. Dumping fallback file locally.")
        with open("fallback_output.json", "w") as f:
            json.dump(dataset, f, indent=4)
        return

    date_str = datetime.now().strftime("%Y-%m-%d")
    target_filename = f"fashion_analytics_{date_str}.json"
    
    with open(target_filename, "w") as tmp:
        json.dump(dataset, tmp, indent=4)

    try:
        sa_info = json.loads(sa_json_str)
        creds = service_account.Credentials.from_service_account_info(
            sa_info, 
            scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        service = build("drive", "v3", credentials=creds)
        
        metadata = {
            "name": target_filename,
            "parents": [folder_id]
        }
        media = MediaFileUpload(target_filename, mimetype="application/json")
        
        uploaded_file = service.files().create(
            body=metadata,
            media_body=media,
            fields="id"
        ).execute()
        print(f"[Success] Batch uploaded. Node ID assigned: {uploaded_file.get('id')}")
        
    except Exception as e:
        print(f"[Error] Storage pipeline upload interrupted: {e}")
        
    finally:
        if os.path.exists(target_filename):
            os.remove(target_filename)
```

### `main.py`
```python
import asyncio
from src.discovery import discover_targets
from src.parser import scrape_and_process_url
from src.gdrive import upload_to_drive

async def main():
    print("Initializing Fashion Analytics Scraper Job Pipeline...")
    
    targets = discover_targets()
    if not targets:
        targets = [
            "https://www.styledumonde.com/",
            "https://www.styleforum.net/forums/classic-menswear-clothing.5/"
        ]
        
    print(f"Target vector established. Scraping {len(targets)} domains...")
    
    aggregated_dataset = []
    for target in targets:
        print(f"Running extract workflow on target: {target}")
        site_records = await scrape_and_process_url(target)
        aggregated_dataset.extend(site_records)
        
    if aggregated_dataset:
        print(f"Extraction successful. Batch contains {len(aggregated_dataset)} items. Storing...")
        upload_to_drive(aggregated_dataset)
    else:
        print("Pipeline execution completed. No new items identified to store.")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 4. Setup Steps

### 1. Google Cloud Configuration
1. Open the **Google Cloud Console**. Create a project and enable the **Google Drive API**.
2. Create a **Service Account** under IAM & Admin.
3. Generate a new **JSON Key** for the service account and download the file.
4. Open your personal Google Drive, create a destination folder, and share it with the service account's email address with **Editor** permissions.
5. Extract the Folder ID from the URL string.

### 2. GitHub Secrets Setup
Add the following repository secrets under **Settings > Secrets and Variables > Actions**:
* `GEMINI_API_KEY`: Your Google Gemini API token.

* `GDRIVE_FOLDER_ID`: The target Google Drive folder ID.
* `GDRIVE_SERVICE_ACCOUNT_JSON`: The full plain-text string contents of the downloaded service account JSON file.