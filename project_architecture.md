# Fashion Analytics Pipeline Architecture

This document describes the structure of the pipeline: the directory layout, the daily and the weekly run, and how failures are handled. The modules are described in [src/README.md](src/README.md).

## Table of Contents
- [1. Directory Structure](#1-directory-structure)
- [2. Daily Run](#2-daily-run)
- [3. Weekly Run](#3-weekly-run)
- [4. Failure Handling](#4-failure-handling)
- [5. Setup Steps](#5-setup-steps)

---

## 1. Directory Structure

```text
style_dataset/
├── .github/
│   ├── dependabot.yml
│   └── workflows/
│       ├── ci.yml                  # lint, tests, commit messages
│       ├── daily_scraper.yml       # daily run, 01:00 UTC
│       ├── email_notify.yml        # commit comment after data commits
│       └── source_discovery.yml    # weekly maintenance, Sundays 12:00 UTC
├── data/
│   ├── YYYY/MM/fashion_analytics_YYYY-MM-DD.json   # records of one UTC day
│   └── state/                      # sources.json, posts.json, images.json, run_log.jsonl
├── scripts/
│   └── try_extraction.py           # manual evaluation of the extraction
├── src/                            # pipeline modules
├── tests/                          # offline unit and integration tests, fixtures
├── pyproject.toml
├── uv.lock
└── README.md
```

---

## 2. Daily Run

`src/main.py` starts the run; `src/pipeline.py` executes it.

1. Load the source registry. If it is empty, seed it from the domains of the legacy records.
2. Fetch the feeds of the active and probation sources and update their feed statistics.
3. Select the posts: published within 30 days, not yet processed or due for a retry, not about clearly unrelated topics; at most 3 per source, 40 per run and 10 from sources on probation.
4. For each post:
   1. Take the content from the feed if it includes images, else render the page with crawl4ai.
   2. Select the article element and extract text, image candidates, links and the canonical URL.
   3. Skip the post if it was already processed under its canonical URL, or if it is republished.
   4. Download the image candidates, filter them by size and shape, and remove duplicates by dHash.
   5. Send all images and the post text to Gemini in one request.
   6. Build the records, without outfits of minors, and merge them into the day file.
   7. Save the post state, the image state and the source registry.
5. Give up pending posts older than 30 days, apply the source lifecycle rules, save, and append a line to the run log.
6. The workflow updates the README overview and commits `data/` and `README.md`.

---

## 3. Weekly Run

`src/discovery.py` maintains the registry:

1. Fetch the feeds of dormant and broken sources and apply the lifecycle rules, so that sources that publish again return.
2. Ask Gemini with Google Search for the country and language of sources whose country was so far inferred only from the domain or the feed language (20 domains per request).
3. Ask Gemini with Google Search for new personal style blogs, with a search focus that changes every week. Evaluate each candidate with the admission rules and store it with its status; at most 10 are admitted per week.

The workflow commits `data/state/`.

---

## 4. Failure Handling

- **One post fails:** the post stays pending and is retried in later runs. Failures that concern the post itself (missing page, unusable model response) count as attempts; after 3 attempts the post is given up. Server errors, rate limits and quota errors do not count.
- **Quota or call budget reached:** extraction stops; all processed posts are saved.
- **Gemini rejects the request** (for example an invalid API key): the run stops, saves its progress and exits with code 1, so that the workflow fails visibly.
- **Run interrupted** between writing records and writing state: the next run processes the post again and replaces its earlier records.
- **Blocked or invalid model response:** the images are split into two requests; images of a failed half are not marked as analyzed.
- **Concurrent pushes:** both data workflows share a concurrency group, pull with rebase and retry the push up to three times.

---

## 5. Setup Steps

1. Add the `GEMINI_API_KEY` repository secret under **Settings > Secrets and variables > Actions**. For local runs, set it in a `.env` file.
2. Allow workflows to push to the repository under **Settings > Actions > General > Workflow permissions**.
3. For local runs, install the browser once: `uv run playwright install chromium`.
