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
**Last Updated:** 2026-10-04 06:44:48 UTC

- **Total Days/Files:** 113
- **Total Outfits:** 1382

| Variable | Description | Fill Rate | Distinct Values |
|----------|-------------|-----------|-----------------|
| `accessories` | List of visible accessories. | 86.8% (1200) | 542 |
| `age_group` | Visually estimated age bracket. | 93.7% (1295) | 6 |
| `bottom_garment_type` | The type of bottom being worn. | 58.8% (813) | 335 |
| `brand_mentions` | Fashion brands explicitly mentioned. | 11.0% (152) | 85 |
| `clothing_fit` | The overall fit of the clothing. | 93.6% (1293) | 4 |
| `clothing_style` | The primary fashion style. | 100.0% (1382) | 264 |
| `color_contrast_strategy` | How colors are paired. | 41.3% (571) | 5 |
| `color_palette_type` | The overall color theory of the outfit. | 93.7% (1295) | 5 |
| `confidence_score` | Model confidence score (0.0 to 1.0). | 100.0% (1382) | 10 |
| `date_scraped` | Automatically injected date. | 100.0% (1382) | 113 |
| `embellishments` | Visible decorative details. | 9.9% (137) | 90 |
| `fabric_textures` | Visually inferred materials. | 93.6% (1293) | 312 |
| `focal_point` | The standout piece that draws the eye. | 93.6% (1293) | 781 |
| `footwear_type` | The type of shoes being worn. | 34.5% (477) | 184 |
| `gender` | The perceived gender of the subject. | 100.0% (1382) | 3 |
| `hair_accessories` | Specific hair accessories. | 2.5% (35) | 21 |
| `hair_color` | Subject's hair color. | 92.6% (1280) | 79 |
| `hair_finish` | The styling finish of the hair. | 41.2% (569) | 6 |
| `hair_parting` | How the hair is parted. | 41.2% (569) | 5 |
| `hairstyle` | The primary hairstyle of the subject. | 100.0% (1382) | 667 |
| `hardware_details` | Visible metal or structural components. | 19.8% (274) | 143 |
| `hemline_length` | The hemline length for bottoms. | 16.7% (231) | 6 |
| `image_url` | Image URL of the subject (GDPR compliant). | 0.9% (13) | 12 |
| `is_trendsetter` | True if celebrity/model/artist, False if regular person. | 100.0% (1382) | 2 |
| `layering_complexity` | Scale from 1 (simple) to 5 (heavy layering). | 93.6% (1294) | 4 |
| `makeup_style` | Subject's makeup style. | 93.6% (1294) | 40 |
| `material_finish` | The optical quality of the fabrics. | 41.2% (569) | 4 |
| `neckline_style` | The cut of the top/dress around the neck. | 38.0% (525) | 7 |
| `occasion` | Intended event or setting for the outfit. | 41.3% (571) | 5 |
| `patterns` | Patterns visible on the clothing. | 93.6% (1294) | 223 |
| `pose_or_activity` | What the subject is doing. | 93.6% (1294) | 280 |
| `price_segment` | Inferred price segment. | 93.7% (1295) | 4 |
| `primary_colors` | List of dominant colors in the outfit. | 100.0% (1382) | 102 |
| `region` | Geographic region identified from context ('EU' or 'US'). | 100.0% (1382) | 2 |
| `seasonality` | The inferred season. | 93.7% (1295) | 5 |
| `sentiment_or_vibe` | The aesthetic vibe described. | 93.5% (1292) | 457 |
| `setting` | The setting or background of the photo. | 93.7% (1295) | 5 |
| `silhouette` | The overall outline or shape of the outfit. | 39.9% (552) | 6 |
| `source_url` | The URL of the webpage where the image was found. | 100.0% (1382) | 215 |
| `subculture_aesthetic` | Specific internet aesthetics or micro-trends. | 5.8% (80) | 44 |
| `top_garment_type` | The type of top being worn. | 92.9% (1284) | 625 |
| `waistline_rise` | The rise of the bottoms. | 25.4% (351) | 3 |
| `weather_conditions` | Inferred weather. | 76.7% (1060) | 43 |
<!-- DATASET_OVERVIEW_END -->

## Pipeline Architecture

```mermaid
sequenceDiagram
    participant Action as GitHub Actions
    participant Main as main.py
    participant Disc as discovery.py
    participant Parse as parser.py
    participant Crawl as Crawl4AI
    participant Gemini as Gemini API
    participant Store as storage.py
    
    Action->>Main: Trigger daily run
    activate Main
    
    loop Max 3 runs (until > 10 items)
        Main->>Disc: discover_targets()
        activate Disc
        Disc->>Gemini: Search web for blogs
        Gemini-->>Disc: Return URLs
        Disc-->>Main: List of target URLs
        deactivate Disc
        
        loop For each URL
            Main->>Parse: scrape_and_process_url(url)
            activate Parse
            Parse->>Crawl: Fetch webpage & extract images
            Crawl-->>Parse: HTML text & filtered images
            Parse->>Gemini: Vision analysis (schema.py)
            Gemini-->>Parse: Validated outfit JSON
            Parse-->>Main: List of FashionRecords
            deactivate Parse
        end
    end
    
    Main->>Store: store_dataset(aggregated_dataset)
    activate Store
    Store-->>Main: Save to disk
    deactivate Store
    
    Main-->>Action: Pipeline complete
    deactivate Main
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

Copyright (c) 2026 Conrad Kleinn. All rights reserved.
