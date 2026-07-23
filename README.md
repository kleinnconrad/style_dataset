# Fashion Analytics Scraper

An autonomous fashion analytics pipeline that runs daily via GitHub Actions. It dynamically discovers independent fashion blogs, scrapes them using `crawl4ai`, extracts fashion metadata using Google Gemini (`gemini-2.5-flash`), and saves the structured data to the repository.

## Table of Contents
- [Dataset Overview](#dataset-overview)
- [Pipeline Architecture](#pipeline-architecture)
- [What It Scrapes](#what-it-scrapes)
- [Dependency Management](#dependency-management)
- [Setup](#setup)

## Dataset Overview
<!-- DATASET_OVERVIEW_START -->
**Last Updated:** 2026-07-23 04:21:35 UTC

- **Total Days/Files:** 40
- **Total Outfits:** 462

| Variable | Description | Fill Rate | Distinct Values |
|----------|-------------|-----------|-----------------|
| `accessories` | List of visible accessories. | 73.6% (340) | 223 |
| `age_group` | Visually estimated age bracket. | 81.2% (375) | 5 |
| `bottom_garment_type` | The type of bottom being worn. | 51.7% (239) | 136 |
| `brand_mentions` | Fashion brands explicitly mentioned. | 8.0% (37) | 32 |
| `clothing_fit` | The overall fit of the clothing. | 81.0% (374) | 4 |
| `clothing_style` | The primary fashion style. | 100.0% (462) | 124 |
| `color_palette_type` | The overall color theory of the outfit. | 81.2% (375) | 5 |
| `confidence_score` | Model confidence score (0.0 to 1.0). | 100.0% (462) | 8 |
| `date_scraped` | Automatically injected date. | 100.0% (462) | 40 |
| `fabric_textures` | Visually inferred materials. | 81.2% (375) | 156 |
| `focal_point` | The standout piece that draws the eye. | 81.2% (375) | 270 |
| `footwear_type` | The type of shoes being worn. | 34.6% (160) | 89 |
| `gender` | The perceived gender of the subject. | 100.0% (462) | 3 |
| `hair_color` | Subject's hair color. | 80.3% (371) | 38 |
| `hairstyle` | The primary hairstyle of the subject. | 100.0% (462) | 267 |
| `image_url` | Image URL of the subject (GDPR compliant). | 1.5% (7) | 6 |
| `is_trendsetter` | True if celebrity/model/artist, False if regular person. | 100.0% (462) | 2 |
| `layering_complexity` | Scale from 1 (simple) to 5 (heavy layering). | 81.2% (375) | 4 |
| `makeup_style` | Subject's makeup style. | 81.2% (375) | 18 |
| `patterns` | Patterns visible on the clothing. | 81.2% (375) | 109 |
| `pose_or_activity` | What the subject is doing. | 81.2% (375) | 123 |
| `price_segment` | Inferred price segment. | 81.2% (375) | 4 |
| `primary_colors` | List of dominant colors in the outfit. | 100.0% (462) | 66 |
| `region` | Geographic region identified from context ('EU' or 'US'). | 100.0% (462) | 2 |
| `seasonality` | The inferred season. | 81.2% (375) | 5 |
| `sentiment_or_vibe` | The aesthetic vibe described. | 80.7% (373) | 170 |
| `setting` | The setting or background of the photo. | 81.2% (375) | 5 |
| `source_url` | The URL of the webpage where the image was found. | 100.0% (462) | 102 |
| `top_garment_type` | The type of top being worn. | 80.1% (370) | 237 |
| `weather_conditions` | Inferred weather. | 60.0% (277) | 24 |
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

## Dependency Management

This project uses the "Lockfile Pattern" via `pip-tools` for reproducible builds and streamlined dependency updates.

1. **Top-Level Dependencies**: Defined in `requirements.in`. This file only lists direct dependencies required by the project.
2. **Pinned Dependencies**: `requirements.txt` is generated automatically from `requirements.in` using `pip-compile`. This locks all dependencies and sub-dependencies to specific versions.
3. **Automated Updates**: Dependabot is configured (`.github/dependabot.yml`) to automatically check for updates weekly and group all Python dependency updates into a single pull request.

To update dependencies locally, modify `requirements.in` and run:
```bash
pip-compile requirements.in
```

## Setup

1. Create a `.env` file for local development or configure GitHub Secrets.
2. Provide your `GEMINI_API_KEY`.
3. The GitHub Actions workflow (`daily_scraper.yml`) runs daily at 00:00 UTC and automatically pushes new data to the `data/` folder.
