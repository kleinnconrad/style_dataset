# Fashion Analytics Scraper

An autonomous fashion analytics pipeline that runs daily via GitHub Actions. It dynamically discovers independent fashion blogs, scrapes them using `crawl4ai`, extracts fashion metadata using Google Gemini (`gemini-2.5-flash`), and saves the structured data to the repository.

## Table of Contents
- [Pipeline Architecture](#pipeline-architecture)
- [What It Scrapes](#what-it-scrapes)
- [Data Schema & Field Definitions](#data-schema--field-definitions)
- [Setup](#setup)

## Pipeline Architecture

```mermaid
flowchart TD
    subgraph Orchestration
        Main[main.py<br>Pipeline Orchestrator]
    end

    subgraph Discovery Phase
        Disc[discovery.py]
        GemSearch{{Gemini Search<br>Dynamic URL Discovery}}
    end

    subgraph Extraction Phase
        Parse[parser.py]
        Crawl[Crawl4AI<br>Anti-bot bypass]
        Filter[Filter Logos &<br>Select Top 3 Images]
        Vision{{Gemini 3.5 Flash<br>Vision Analysis}}
        Schema{schema.py<br>Is Valid Outfit?}
    end

    subgraph Storage Phase
        Store[storage.py]
        Git[(GitHub 'data/' Folder)]
        Local[(Local Downloads)]
    end

    Main -->|1. Init| Disc
    Disc --> GemSearch
    GemSearch -->|Target URLs| Main
    
    Main -->|2. Scrape| Parse
    Parse --> Crawl
    Crawl -->|Images & Context| Filter
    Filter --> Vision
    Vision --> Schema
    
    Schema -->|Yes: FashionRecord| Main
    Schema -->|No: Discard| Discard([Skip])

    Main -->|3. Save| Store
    Store -->|GitHub Actions| Git
    Store -->|Local PC| Local

    classDef script fill:#2b3137,stroke:#24292e,stroke-width:2px,color:#fff;
    classDef model fill:#1a73e8,stroke:#1558d6,stroke-width:2px,color:#fff;
    classDef logic fill:#005cc5,stroke:#004491,stroke-width:2px,color:#fff;
    
    class Main,Disc,Parse,Store,Schema script;
    class GemSearch,Vision model;
    class Crawl,Filter,Git,Local logic;
```

## What It Scrapes

The scraper focuses on independent fashion blogs and forums. To ensure enough data is collected daily, the pipeline uses a **multi-run retry loop**:
1. **Dynamic Discovery**: A Gemini-powered search identifies small, active fashion blogs.
2. **Page Crawling**: `crawl4ai` fetches the target URL and extracts all images and readable markdown text.
3. **Filtering**: It ignores any image containing the word "logo" in its URL to ensure it captures actual content photos.
4. **Context & Vision Extraction**: It takes the **first 3 viable images** and the first 1000 characters of the webpage's text. These are sent to the Gemini Vision model for AI analysis based on a strict fashion taxonomy.
5. **Validation Check**: Images flagged as non-outfits (e.g. flat lays, products, landscapes) are explicitly rejected.
6. **Adaptive Retries**: If the entire batch yields **10 items or less**, the pipeline automatically launches another discovery run (instructing Gemini to find *different* URLs) and crawls again. It will attempt this up to **3 times** to reach the quota before terminating.

## Data Schema & Field Definitions

When Gemini processes an image and its context, it produces a structured JSON record. Below is an explanation of every field in the output and how it is populated:

* **`date_scraped`** (String): 
  * *Example*: `"2026-06-14"`
  * *How it's populated*: Automatically injected by the script (`datetime.now()`) on the day the crawl runs.
* **`source_url`** (String): 
  * *Example*: `"https://freelancersfashion.blogspot.com"`
  * *How it's populated*: The URL of the webpage where the image was found.
* **`clothing_style`** (String): 
  * *Example*: `"Corporate"`
  * *How it's populated*: Gemini analyzes the clothing in the image against a predefined list of styles (e.g., Casual, Haute Couture, Corporate, Streetwear) and selects the best match.
* **`hairstyle`** (String): 
  * *Example*: `"Updo"`
  * *How it's populated*: Gemini analyzes the subject's hair and selects a category (e.g., Updo, Long, Short, Unidentifiable).
* **`gender`** (String): 
  * *Example*: `"Female"`
  * *How it's populated*: Gemini analyzes the subject and categorizes their perceived gender (`"Male"`, `"Female"`, or `"Unidentifiable"`).
* **`primary_colors`** (Array of Strings): 
  * *Example*: `["pink", "brown", "beige", "blue", "green"]`
  * *How it's populated*: Gemini detects the dominant colors of the outfit worn by the subject.
* **`is_trendsetter`** (Boolean): 
  * *Example*: `false`
  * *How it's populated*: The script analyzes the 1000-character text context. If the text contains keywords like "runway", "celebrity", "red carpet", or "model", this is set to `true`. Otherwise, it defaults to `false`.
* **`region`** (String): 
  * *Example*: `"EU"`
  * *How it's populated*: The script checks the text context for location keywords (like "paris", "milan", "london", "eu"). If found, it's flagged as `"EU"`, otherwise it defaults to `"US"`.
* **`confidence_score`** (Float): 
  * *Example*: `0.95`
  * *How it's populated*: Gemini assigns a confidence score (from 0.0 to 1.0) based on how clearly it can identify the fashion taxonomy in the image.
* **`image_url`** (String or Null): 
  * *Example*: `null`
  * *How it's populated*: For **GDPR Compliance**, the actual image URL is only retained if `is_trendsetter` is `true` (i.e., the person is a public figure or model). If they are a private citizen (`is_trendsetter = false`), the image URL is explicitly stripped and set to `null`.
* **`top_garment_type`** (String or Null): e.g., `"Blazer"`, `"Chunky Knit Sweater"`.
* **`bottom_garment_type`** (String or Null): e.g., `"Wide-leg jeans"`, `"Cargo pants"`.
* **`footwear_type`** (String or Null): e.g., `"Sneakers"`, `"Loafers"`.
* **`accessories`** (Array of Strings): e.g., `["Sunglasses", "Crossbody bag"]`.
* **`patterns`** (Array of Strings): e.g., `["Striped", "Floral"]`.
* **`fabric_textures`** (Array of Strings): e.g., `["Denim", "Leather"]`.
* **`clothing_fit`** (String or Null): e.g., `"Oversized"`, `"Fitted/Tight"`.
* **`setting`** (String or Null): e.g., `"Urban/Street"`, `"Indoors/Studio"`.
* **`seasonality`** (String or Null): e.g., `"Summer"`, `"Winter"`.
* **`weather_conditions`** (String or Null): e.g., `"Sunny"`, `"Overcast"`.
* **`pose_or_activity`** (String or Null): e.g., `"Walking confidently"`.
* **`age_group`** (String or Null): e.g., `"Young Adult"`, `"Senior"`.
* **`makeup_style`** (String or Null): e.g., `"Natural"`, `"Bold lips"`.
* **`hair_color`** (String or Null): e.g., `"Blonde"`, `"Brunette"`.
* **`brand_mentions`** (Array of Strings): Extracted from text context, e.g., `["Gucci", "Zara"]`.
* **`price_segment`** (String or Null): e.g., `"Luxury"`, `"Fast Fashion"`.
* **`sentiment_or_vibe`** (String or Null): e.g., `"Effortless chic"`.
* **`color_palette_type`** (String or Null): e.g., `"Monochrome"`, `"Pastel"`.
* **`layering_complexity`** (Integer or Null): e.g., `2`.
* **`focal_point`** (String or Null): e.g., `"Bright red handbag"`.
## Dataset Overview
<!-- DATASET_OVERVIEW_START -->
**Last Updated:** 2026-07-06 13:56:58 UTC

- **Total Days/Files:** 24
- **Total Outfits:** 269

| Variable | Fill Rate | Distinct Values |
|----------|-----------|-----------------|
| `accessories` | 61.7% (166) | 117 |
| `age_group` | 67.7% (182) | 2 |
| `bottom_garment_type` | 46.1% (124) | 70 |
| `brand_mentions` | 6.7% (18) | 17 |
| `clothing_fit` | 67.7% (182) | 4 |
| `clothing_style` | 100.0% (269) | 84 |
| `color_palette_type` | 67.7% (182) | 5 |
| `confidence_score` | 100.0% (269) | 7 |
| `date_scraped` | 100.0% (269) | 23 |
| `fabric_textures` | 67.7% (182) | 72 |
| `focal_point` | 67.3% (181) | 130 |
| `footwear_type` | 30.5% (82) | 54 |
| `gender` | 100.0% (269) | 3 |
| `hair_color` | 67.3% (181) | 21 |
| `hairstyle` | 100.0% (269) | 163 |
| `image_url` | 1.1% (3) | 3 |
| `is_trendsetter` | 100.0% (269) | 2 |
| `layering_complexity` | 67.7% (182) | 3 |
| `makeup_style` | 67.7% (182) | 11 |
| `patterns` | 67.7% (182) | 67 |
| `pose_or_activity` | 67.7% (182) | 65 |
| `price_segment` | 67.7% (182) | 4 |
| `primary_colors` | 100.0% (269) | 48 |
| `region` | 100.0% (269) | 2 |
| `seasonality` | 67.7% (182) | 5 |
| `sentiment_or_vibe` | 66.9% (180) | 91 |
| `setting` | 67.7% (182) | 5 |
| `source_url` | 100.0% (269) | 76 |
| `top_garment_type` | 66.2% (178) | 127 |
| `weather_conditions` | 46.1% (124) | 11 |
<!-- DATASET_OVERVIEW_END -->

## Setup

1. Create a `.env` file for local development or configure GitHub Secrets.
2. Provide your `GEMINI_API_KEY`.
3. The GitHub Actions workflow (`daily_scraper.yml`) runs daily at 00:00 UTC and automatically pushes new data to the `data/` folder.
