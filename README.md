# Fashion Analytics Scraper

An autonomous fashion analytics pipeline that runs daily via GitHub Actions. It dynamically discovers independent fashion blogs, scrapes them using `crawl4ai`, extracts fashion metadata using Google Gemini (`gemini-2.5-flash`), and saves the structured data to the repository.

## Table of Contents
- [Dataset Overview](#dataset-overview)
- [Pipeline Architecture](#pipeline-architecture)
- [What It Scrapes](#what-it-scrapes)
- [Setup](#setup)

## Dataset Overview
<!-- DATASET_OVERVIEW_START -->
**Last Updated:** 2026-07-07 11:42:23 UTC

- **Total Days/Files:** 24
- **Total Outfits:** 270

| Variable | Description | Fill Rate | Distinct Values |
|----------|-------------|-----------|-----------------|
| `accessories` | List of visible accessories. | 61.5% (166) | 126 |
| `age_group` | Visually estimated age bracket. | 67.8% (183) | 3 |
| `bottom_garment_type` | The type of bottom being worn. | 46.7% (126) | 72 |
| `brand_mentions` | Fashion brands explicitly mentioned. | 7.8% (21) | 22 |
| `clothing_fit` | The overall fit of the clothing. | 67.8% (183) | 4 |
| `clothing_style` | The primary fashion style. | 100.0% (270) | 88 |
| `color_palette_type` | The overall color theory of the outfit. | 67.8% (183) | 5 |
| `confidence_score` | Model confidence score (0.0 to 1.0). | 100.0% (270) | 7 |
| `date_scraped` | Automatically injected date. | 100.0% (270) | 24 |
| `fabric_textures` | Visually inferred materials. | 67.8% (183) | 72 |
| `focal_point` | The standout piece that draws the eye. | 67.8% (183) | 133 |
| `footwear_type` | The type of shoes being worn. | 30.4% (82) | 52 |
| `gender` | The perceived gender of the subject. | 100.0% (270) | 3 |
| `hair_color` | Subject's hair color. | 67.4% (182) | 23 |
| `hairstyle` | The primary hairstyle of the subject. | 100.0% (270) | 162 |
| `image_url` | Image URL of the subject (GDPR compliant). | 1.9% (5) | 4 |
| `is_trendsetter` | True if celebrity/model/artist, False if regular person. | 100.0% (270) | 2 |
| `layering_complexity` | Scale from 1 (simple) to 5 (heavy layering). | 67.8% (183) | 3 |
| `makeup_style` | Subject's makeup style. | 67.8% (183) | 12 |
| `patterns` | Patterns visible on the clothing. | 67.8% (183) | 67 |
| `pose_or_activity` | What the subject is doing. | 67.8% (183) | 70 |
| `price_segment` | Inferred price segment. | 67.8% (183) | 4 |
| `primary_colors` | List of dominant colors in the outfit. | 100.0% (270) | 49 |
| `region` | Geographic region identified from context ('EU' or 'US'). | 100.0% (270) | 2 |
| `seasonality` | The inferred season. | 67.8% (183) | 5 |
| `sentiment_or_vibe` | The aesthetic vibe described. | 67.0% (181) | 98 |
| `setting` | The setting or background of the photo. | 67.8% (183) | 5 |
| `source_url` | The URL of the webpage where the image was found. | 100.0% (270) | 78 |
| `top_garment_type` | The type of top being worn. | 66.7% (180) | 132 |
| `weather_conditions` | Inferred weather. | 46.7% (126) | 10 |
<!-- DATASET_OVERVIEW_END -->

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

## Setup

1. Create a `.env` file for local development or configure GitHub Secrets.
2. Provide your `GEMINI_API_KEY`.
3. The GitHub Actions workflow (`daily_scraper.yml`) runs daily at 00:00 UTC and automatically pushes new data to the `data/` folder.
