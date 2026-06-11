# Fashion Analytics Scraper

An autonomous fashion analytics pipeline that runs daily via GitHub Actions, scrapes targeted fashion platforms using `crawl4ai`, extracts fashion metadata using Google Gemini `gemini-3.1-pro`, and saves the structured data to Google Drive.

## GDPR Compliance
To comply with GDPR, images of regular people are not retained in the dataset. Only image URLs for identified trendsetters/celebrities are stored.

## Setup
1. Define Google Drive service account and folder ID.
2. Setup GitHub Secrets for `GEMINI_API_KEY`, `GDRIVE_FOLDER_ID`, and `GDRIVE_SERVICE_ACCOUNT_JSON`.
3. The workflow runs daily at 00:00 UTC.
