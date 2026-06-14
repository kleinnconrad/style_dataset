import os
import json
from pathlib import Path
from datetime import datetime

def store_dataset(dataset: list[dict]):
    """Stores the dataset either in GitHub's data/ folder or the local Downloads folder."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    target_filename = f"fashion_analytics_{date_str}.json"
    
    if os.getenv("GITHUB_ACTIONS") == "true":
        target_dir = Path("data")
    else:
        # Fallback to local Downloads directory
        target_dir = Path.home() / "Downloads"
        
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / target_filename
    
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=4)
        
    print(f"[Success] Dataset securely stored to: {target_path}")
