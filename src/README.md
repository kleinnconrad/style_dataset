# Fashion Analytics Pipeline: `src/` Directory

This directory contains the core logic for the autonomous fashion analytics scraper. The architecture is decoupled into specific functional modules that handle target discovery, data extraction, validation, and storage.

## Table of Contents
- [Files and Modules](#files-and-modules)
  - [`main.py`](#mainpy)
  - [`discovery.py`](#discoverypy)
  - [`parser.py`](#parserpy)
  - [`schema.py`](#schemapy)
  - [`storage.py`](#storagepy)
  - [`__init__.py`](#__init__py)

## Files and Modules

### `main.py`
The primary orchestrator and entry point for the application. When executed (`python src/main.py`), it:
1. Calls the discovery script to find targets.
2. Iterates over the targets and launches asynchronous web crawling and vision extraction jobs.
3. Aggregates the parsed results and passes them to the storage module.

### `discovery.py`
Responsible for dynamically finding target websites.
* **How it works:** Uses the `google-genai` SDK and Google Search grounding to search the web for active, independent fashion blogs or "outfit of the day" forums. 
* **Key Features:** Includes an automatic `@retry` mechanism using `tenacity` with exponential backoff to handle temporary Gemini API rate limits (like `503` errors) gracefully.

### `parser.py`
Responsible for extracting and categorizing fashion data.
* **How it works:** Uses `crawl4ai`'s `AsyncWebCrawler` with `magic=True` to bypass Cloudflare and anti-bot protections. It grabs the text context and extracts image URLs.
* **Vision Processing:** Sends the images and context directly to the Gemini API (`gemini-2.5-flash`), instructing it to map the image contents strictly to the predefined schema. Also utilizes `tenacity` retries to stabilize API interactions.

### `schema.py`
Defines the strict structural rules for the AI's output.
* **How it works:** Uses Pydantic to create the `FashionRecord` model. This forces the Gemini API to respond in a predictable, validated JSON format (e.g., constraining `clothing_style` or `region` to specific categories, and handling GDPR-compliant image omission for non-public figures).

### `storage.py`
Handles saving the finalized dataset.
* **How it works:** Uses environment variables to figure out where the code is running.
  * **GitHub Actions:** Saves the JSON files to a local `data/` folder in the project root, which is then committed to the repository by the CI/CD pipeline.
  * **Local Environment:** Automatically falls back to saving the dataset securely in your computer's `Downloads` folder.

### `__init__.py`
An empty file that simply tells Python to treat the `src/` directory as an importable module package.
