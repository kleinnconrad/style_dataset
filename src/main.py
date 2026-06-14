"""
Entry point for the Fashion Analytics Scraper Job Pipeline.
"""
import asyncio
import logging
from dotenv import load_dotenv
from discovery import discover_targets
from parser import scrape_and_process_url
from storage import store_dataset

# Load environment variables from .env file (if it exists)
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main() -> None:
    """Orchestrates the discovery, parsing, and storage pipeline."""
    logger.info("Initializing Fashion Analytics Scraper Job Pipeline...")
    
    aggregated_dataset = []
    visited_targets = []
    max_runs = 3
    
    for run in range(1, max_runs + 1):
        logger.info("\n--- Scraping Run %d/%d ---", run, max_runs)
        targets = discover_targets(ignore_urls=visited_targets)
        
        if not targets:
            targets = [
                "https://www.styledumonde.com/",
                "https://www.styleforum.net/forums/classic-menswear-clothing.5/"
            ]
            
        logger.info("Target vector established. Scraping %d domains...", len(targets))
        
        for target in targets:
            if target in visited_targets:
                continue
            visited_targets.append(target)
            
            logger.info("Running extract workflow on target: %s", target)
            site_records = await scrape_and_process_url(target)
            aggregated_dataset.extend(site_records)
            
        logger.info("Current total extracted items: %d", len(aggregated_dataset))
        
        if len(aggregated_dataset) > 10:
            logger.info("Target item count reached. Stopping discovery phase.")
            break
        elif run < max_runs:
            logger.info("Less than 11 items found. Launching fallback discovery for more targets...")
            
    if aggregated_dataset:
        logger.info("\nExtraction successful. Final batch contains %d items. Storing...", len(aggregated_dataset))
        store_dataset(aggregated_dataset)
    else:
        logger.info("\nPipeline execution completed. No new items identified to store.")

if __name__ == "__main__":
    asyncio.run(main())
