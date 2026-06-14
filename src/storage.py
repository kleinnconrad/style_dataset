"""
Handles dataset serialization and storage execution.
"""
import os
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def store_dataset(dataset: List[Dict[str, Any]]) -> None:
    """
    Stores the dataset either in GitHub's data/ folder or the local Downloads folder.
    
    Args:
        dataset (List[Dict[str, Any]]): The aggregated list of extracted fashion records.
    """
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
        
    logger.info("Dataset securely stored to: %s", target_path)
