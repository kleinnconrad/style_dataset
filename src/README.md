# Fashion Analytics Pipeline: `src/` Directory

This directory contains the modules of the pipeline. The modules import each other as top-level modules; the entry points are `main.py` (daily run), `discovery.py` (weekly run) and `generate_dataset_overview.py` (README overview).

## Table of Contents
- [Entry Points](#entry-points)
  - [`main.py`](#mainpy)
  - [`discovery.py`](#discoverypy)
  - [`generate_dataset_overview.py`](#generate_dataset_overviewpy)
- [Run Orchestration](#run-orchestration)
  - [`pipeline.py`](#pipelinepy)
  - [`settings.py`](#settingspy)
- [Sources and Posts](#sources-and-posts)
  - [`sources.py`](#sourcespy)
  - [`feeds.py`](#feedspy)
  - [`crawl.py`](#crawlpy)
  - [`content.py`](#contentpy)
  - [`images.py`](#imagespy)
- [Extraction and Records](#extraction-and-records)
  - [`schema.py`](#schemapy)
  - [`extraction.py`](#extractionpy)
  - [`records.py`](#recordspy)
- [Storage](#storage)
  - [`storage.py`](#storagepy)
  - [`state.py`](#statepy)
  - [`urls.py`](#urlspy)

## Entry Points

### `main.py`
Starts the daily run. Options: `--dry-run` (no model calls, writes a report), `--max-posts`, `--source` and `--output-dir`. Exits with code 1 if Gemini rejects requests and with code 2 if the API key is missing.

### `discovery.py`
Weekly maintenance of the source registry: re-checks dormant and broken sources, looks up countries with Gemini and Google Search, and discovers and evaluates new blogs. Option: `--output-dir`.

### `generate_dataset_overview.py`
Writes the overview between the markers in the repository's `README.md`: summary, source registry, field table with the descriptions from `schema.py`, garment table and a check for images that appear in more than one post.

## Run Orchestration

### `pipeline.py`
Executes one daily run (`DailyRun`): refreshes the feeds, selects the posts, processes each post and saves records and state after every post. External services (feeds, pages, images, Gemini) are passed in as `Services`, so that tests can replace them. Counters are written to the run log.

### `settings.py`
Settings that may change between runs, read from environment variables, and the output folder (`data/` in GitHub Actions, `~/Downloads/style_dataset/` locally).

## Sources and Posts

### `sources.py`
The source registry (`state/sources.json`) and the lifecycle rules: admission, probation, retirement, dormancy and broken feeds. Infers countries from domains and feed language tags, and seeds the registry from the legacy records.

### `feeds.py`
Finds the feed of a blog (linked feeds, then common WordPress, Blogger and Squarespace paths), parses RSS and Atom, filters posts on unrelated topics and selects the posts of a run with per-source caps, rotation and the probation quota.

### `crawl.py`
Fetches the rendered HTML of post pages with crawl4ai and checks the HTTP status.

### `content.py`
Selects the article element of a page and extracts its text, image candidates (with `srcset`, lazy-loading attributes and captions), external and shopping links and the canonical URL. Detects republished posts from WordPress upload paths.

### `images.py`
Downloads image candidates, applies the EXIF orientation, filters by size and shape, computes the dHash and removes duplicates within the post and against the analyzed images.

## Extraction and Records

### `schema.py`
The Pydantic models of schema 2.0: the models that Gemini fills (`PostExtraction`, `OutfitExtraction`, `Garment`) and the stored record (`OutfitRecord`), with fixed vocabularies, the garment categories and the country-to-region map.

### `extraction.py`
Sends the images and text of a post to Gemini in one request with structured output. Paces the requests, handles quota and server errors, retries a blocked or invalid response with the images split into two requests, and cleans the response (image numbers, text, brands).

### `records.py`
Builds the stored records from a cleaned response and the post and source metadata. Outfits of children and teenagers are not stored.

## Storage

### `storage.py`
Merges records into the file of their UTC day. Records of a reprocessed post replace its earlier records; schema 1.x records are kept unchanged.

### `state.py`
Post state (`state/posts.json`) and image state (`state/images.json`) with attempt rules and near-duplicate lookup, atomic writes, and the run log.

### `urls.py`
Normalizes URLs to comparison keys and derives the domain of a source.
