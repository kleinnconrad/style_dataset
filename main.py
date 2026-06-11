import asyncio
from src.discovery import discover_targets
from src.parser import scrape_and_process_url
from src.gdrive import upload_to_drive

async def main():
    print("Initializing Fashion Analytics Scraper Job Pipeline...")
    
    targets = discover_targets()
    if not targets:
        targets = [
            "https://www.styledumonde.com/",
            "https://www.styleforum.net/forums/classic-menswear-clothing.5/"
        ]
        
    print(f"Target vector established. Scraping {len(targets)} domains...")
    
    aggregated_dataset = []
    for target in targets:
        print(f"Running extract workflow on target: {target}")
        site_records = await scrape_and_process_url(target)
        aggregated_dataset.extend(site_records)
        
    if aggregated_dataset:
        print(f"Extraction successful. Batch contains {len(aggregated_dataset)} items. Storing...")
        upload_to_drive(aggregated_dataset)
    else:
        print("Pipeline execution completed. No new items identified to store.")

if __name__ == "__main__":
    asyncio.run(main())
