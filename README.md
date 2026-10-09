# Fashion Analytics Scraper
[![CI](https://github.com/kleinnconrad/style_dataset/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kleinnconrad/style_dataset/actions/workflows/ci.yml)

An autonomous fashion analytics pipeline that runs daily via GitHub Actions. It dynamically discovers independent fashion blogs, scrapes them using `crawl4ai`, extracts fashion metadata using Google Gemini (`gemini-2.5-flash`), and saves the structured data to the repository.

## Table of Contents
- [Dataset Overview](#dataset-overview)
- [Pipeline Architecture](#pipeline-architecture)
- [What It Scrapes](#what-it-scrapes)
- [Dependency Management](#dependency-management)
- [Continuous Integration](#continuous-integration)
- [Setup](#setup)

## Dataset Overview
<!-- DATASET_OVERVIEW_START -->
**Last Updated:** 2026-10-09 07:20:54 UTC

- **Total Days/Files:** 118
- **Total Outfits:** 1443

| Variable | Description | Fill Rate | Distinct Values |
|----------|-------------|-----------|-----------------|
| `accessories` | List of visible accessories. | 86.6% (1249) | 557 |
| `age_group` | Visually estimated age bracket. | 94.0% (1356) | 6 |
| `bottom_garment_type` | The type of bottom being worn. | 59.6% (860) | 339 |
| `brand_mentions` | Fashion brands explicitly mentioned. | 11.4% (164) | 90 |
| `clothing_fit` | The overall fit of the clothing. | 93.8% (1354) | 4 |
| `clothing_style` | The primary fashion style. | 100.0% (1443) | 266 |
| `color_contrast_strategy` | How colors are paired. | 43.8% (632) | 5 |
| `color_palette_type` | The overall color theory of the outfit. | 94.0% (1356) | 5 |
| `confidence_score` | Model confidence score (0.0 to 1.0). | 100.0% (1443) | 10 |
| `date_scraped` | Automatically injected date. | 100.0% (1443) | 118 |
| `embellishments` | Visible decorative details. | 9.9% (143) | 93 |
| `fabric_textures` | Visually inferred materials. | 93.8% (1354) | 322 |
| `focal_point` | The standout piece that draws the eye. | 93.8% (1354) | 814 |
| `footwear_type` | The type of shoes being worn. | 34.9% (504) | 190 |
| `gender` | The perceived gender of the subject. | 100.0% (1443) | 3 |
| `hair_accessories` | Specific hair accessories. | 2.4% (35) | 21 |
| `hair_color` | Subject's hair color. | 92.9% (1341) | 79 |
| `hair_finish` | The styling finish of the hair. | 43.7% (630) | 6 |
| `hair_parting` | How the hair is parted. | 43.7% (630) | 5 |
| `hairstyle` | The primary hairstyle of the subject. | 100.0% (1443) | 691 |
| `hardware_details` | Visible metal or structural components. | 20.5% (296) | 148 |
| `hemline_length` | The hemline length for bottoms. | 17.6% (254) | 6 |
| `image_url` | Image URL of the subject (GDPR compliant). | 0.9% (13) | 12 |
| `is_trendsetter` | True if celebrity/model/artist, False if regular person. | 100.0% (1443) | 2 |
| `layering_complexity` | Scale from 1 (simple) to 5 (heavy layering). | 93.9% (1355) | 4 |
| `makeup_style` | Subject's makeup style. | 93.9% (1355) | 40 |
| `material_finish` | The optical quality of the fabrics. | 43.7% (630) | 4 |
| `neckline_style` | The cut of the top/dress around the neck. | 40.2% (580) | 7 |
| `occasion` | Intended event or setting for the outfit. | 43.8% (632) | 5 |
| `patterns` | Patterns visible on the clothing. | 93.9% (1355) | 233 |
| `pose_or_activity` | What the subject is doing. | 93.9% (1355) | 291 |
| `price_segment` | Inferred price segment. | 94.0% (1356) | 4 |
| `primary_colors` | List of dominant colors in the outfit. | 100.0% (1443) | 106 |
| `region` | Geographic region identified from context ('EU' or 'US'). | 100.0% (1443) | 2 |
| `seasonality` | The inferred season. | 94.0% (1356) | 5 |
| `sentiment_or_vibe` | The aesthetic vibe described. | 93.8% (1353) | 468 |
| `setting` | The setting or background of the photo. | 94.0% (1356) | 5 |
| `silhouette` | The overall outline or shape of the outfit. | 42.4% (612) | 6 |
| `source_url` | The URL of the webpage where the image was found. | 100.0% (1443) | 221 |
| `subculture_aesthetic` | Specific internet aesthetics or micro-trends. | 6.1% (88) | 48 |
| `top_garment_type` | The type of top being worn. | 93.2% (1345) | 650 |
| `waistline_rise` | The rise of the bottoms. | 27.2% (392) | 3 |
| `weather_conditions` | Inferred weather. | 77.2% (1114) | 45 |
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

This project uses [uv](https://docs.astral.sh/uv/) with a lockfile for reproducible builds.

1. **Dependencies**: `pyproject.toml` declares the direct dependencies; development tools (pytest, ruff) are in the `dev` dependency group.
2. **Lockfile**: `uv.lock` pins the complete dependency tree. After changing `pyproject.toml`, run `uv lock` and commit both files.
3. **Automated Updates**: Dependabot (`.github/dependabot.yml`) checks weekly for updates of the Python dependencies (`uv` ecosystem, updating `pyproject.toml` and `uv.lock` together) and of the GitHub Actions, each grouped into a single pull request.

## Continuous Integration

The `CI` workflow (`.github/workflows/ci.yml`) runs on every pull request and on pushes to `main`:

| Job | Checks |
|---|---|
| `Lint` | `uv.lock` matches `pyproject.toml`; `ruff check`; `actionlint` (including `shellcheck`) on the workflow files |
| `Tests` | The tests in `tests/` on Python 3.11 and 3.14. They currently verify that every module in `src/` imports with the locked dependencies. |
| `Conventional Commits` | Every commit of a pull request follows the Conventional Commits format (pull requests only) |
| `CI passed` | Succeeds only if all jobs above succeeded or were skipped; use it as the single required status check in the branch protection of `main` |

Run the Python checks locally before pushing:
```bash
uv lock --check
uv run ruff check .
uv run pytest
```

## Setup

1. Create a `.env` file for local development or configure GitHub Secrets.
2. Provide your `GEMINI_API_KEY`.
3. The GitHub Actions workflow (`daily_scraper.yml`) runs daily at 00:00 UTC and automatically pushes new data to the `data/` folder.

Copyright (c) 2026 Conrad Kleinn. All rights reserved.
