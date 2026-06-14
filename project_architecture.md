# Fashion Analytics Pipeline Architecture

This document specifies the decoupled, agentic architecture for an autonomous fashion analytics pipeline that runs daily via GitHub Actions and stores structured data securely.

## Table of Contents
- [1. Project Directory Structure](#1-project-directory-structure)
- [2. Configuration & Workflow Files](#2-configuration--workflow-files)
- [3. Core Source Code Modules](#3-core-source-code-modules)
- [4. Setup Steps](#4-setup-steps)

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
│   ├── main.py
│   ├── parser.py
│   ├── schema.py
│   └── storage.py
├── data/
│   └── (Auto-generated JSON datasets)
├── tests/
├── README.md
└── requirements.txt
```

---

## 2. Configuration & Workflow Files

### `requirements.txt`
Specifies the necessary dependencies for the project, including `crawl4ai`, `pydantic`, `google-genai`, `requests`, and `tenacity`.

### `.github/workflows/daily_scraper.yml`
An automated GitHub Actions workflow configured to execute the pipeline daily at 00:00 UTC. It installs dependencies, runs the extraction via `src/main.py`, and automatically commits and pushes the generated dataset artifacts into the `data/` directory.

---

## 3. Core Source Code Modules

### `src/schema.py`
Defines the strict structural rules for the AI's output using Pydantic (`FashionRecord`). This forces the Gemini API to respond in a predictable, validated JSON format.

### `src/discovery.py`
Responsible for dynamically finding target websites using the `google-genai` SDK and Google Search grounding to search the web for active, independent fashion blogs. Features exponential backoff (`tenacity`) to handle API limits.

### `src/parser.py`
Manages the extraction and categorization of fashion data. Utilizes `crawl4ai`'s `AsyncWebCrawler` with anti-bot bypass parameters. Dispatches the extracted images and text contexts to the Gemini Vision API for classification according to `schema.py`.

### `src/storage.py`
Handles saving the finalized dataset. It detects the execution environment and directs the JSON output either to the local `Downloads` directory (when run locally) or the `data/` directory (when executed within GitHub Actions).

### `src/main.py`
The primary orchestrator. Iterates over discovered targets, executes asynchronous web crawling and vision extraction jobs, and processes retries if the total item count falls below required thresholds. Finally, it aggregates the results and triggers `storage.py`.

---

## 4. Setup Steps

### 1. Environment Variables
1. Set `GEMINI_API_KEY` in a local `.env` file or export it directly in your shell.

### 2. GitHub Secrets Setup
1. Add the `GEMINI_API_KEY` repository secret under **Settings > Secrets and Variables > Actions**.
2. Ensure repository workflow permissions are set to allow Actions to push commits to the repository to update the `data/` directory.