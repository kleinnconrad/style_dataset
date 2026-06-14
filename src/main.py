import asyncio
from dotenv import load_dotenv
from discovery import discover_targets
from parser import scrape_and_process_url
from storage import store_dataset

# Load environment variables from .env file (if it exists)
load_dotenv()

async def main():
    print("Initializing Fashion Analytics Scraper Job Pipeline...")
    
    aggregated_dataset = []
    visited_targets = []
    max_runs = 3
    
    for run in range(1, max_runs + 1):
        print(f"\n--- Scraping Run {run}/{max_runs} ---")
        targets = discover_targets(ignore_urls=visited_targets)
        
        if not targets:
            targets = [
                "https://www.styledumonde.com/",
                "https://www.styleforum.net/forums/classic-menswear-clothing.5/"
            ]
            
        print(f"Target vector established. Scraping {len(targets)} domains...")
        
        for target in targets:
            if target in visited_targets:
                continue
            visited_targets.append(target)
            
            print(f"Running extract workflow on target: {target}")
            site_records = await scrape_and_process_url(target)
            aggregated_dataset.extend(site_records)
            
        print(f"Current total extracted items: {len(aggregated_dataset)}")
        
        if len(aggregated_dataset) > 10:
            print("Target item count reached. Stopping discovery phase.")
            break
        elif run < max_runs:
            print("Less than 11 items found. Launching fallback discovery for more targets...")
            
    if aggregated_dataset:
        print(f"\nExtraction successful. Final batch contains {len(aggregated_dataset)} items. Storing...")
        store_dataset(aggregated_dataset)
    else:
        print("\nPipeline execution completed. No new items identified to store.")

if __name__ == "__main__":
    asyncio.run(main())
