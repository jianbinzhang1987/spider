import os
import sys
import json
import time
import logging
from datetime import datetime
from typing import List, Dict, Any
from playwright.sync_api import sync_playwright

# Setup paths
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import CREDENTIALS, SESSIONS_DIR
from utils.excel_parser import read_purchase_list, save_comparison_results
from scrapers.domestic.szlcsc import SzlcscScraper
from scrapers.domestic.ickey import IckeyScraper
from scrapers.domestic.hqew import HqewScraper
from scrapers.international.mouser import MouserScraper
from scrapers.international.digikey import DigikeyScraper

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("CompareToolMain")

PROGRESS_CACHE_FILE = os.path.join(SESSIONS_DIR, "progress_cache.json")

def load_progress_cache() -> Dict[str, Any]:
    """
    Load already scraped results to support breakpoint resume.
    """
    if os.path.exists(PROGRESS_CACHE_FILE):
        try:
            with open(PROGRESS_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load progress cache: {e}")
    return {}

def save_progress_cache(cache: Dict[str, Any]):
    """
    Save progress cache.
    """
    try:
        os.makedirs(os.path.dirname(PROGRESS_CACHE_FILE), exist_ok=True)
        with open(PROGRESS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.warning(f"Failed to save progress cache: {e}")

def run_compare_tool(
    input_excel_path: str, 
    output_excel_path: str, 
    active_sites: List[str] = None, 
    headless: bool = False,
    progress_callback=None
) -> List[Dict[str, Any]]:
    """
    Main orchestration function.
    """
    if active_sites is None:
        active_sites = ["szlcsc", "ickey", "hqew", "mouser", "digikey"]

    logger.info(f"Loading input BOM from: {input_excel_path}")
    try:
        parts_list = read_purchase_list(input_excel_path)
    except Exception as e:
        logger.error(f"Failed to read input Excel: {e}")
        raise e

    # Initialize Scrapers
    scraper_classes = {
        "szlcsc": SzlcscScraper,
        "ickey": IckeyScraper,
        "hqew": HqewScraper,
        "mouser": MouserScraper,
        "digikey": DigikeyScraper
    }
    
    scrapers = {}
    for site_id in active_sites:
        if site_id in scraper_classes:
            scrapers[site_id] = scraper_classes[site_id](headless=headless)

    # Load cache
    progress_cache = load_progress_cache()
    all_results = []
    
    total_steps = len(parts_list) * len(scrapers)
    current_step = 0

    logger.info("Initializing Playwright...")
    with sync_playwright() as playwright:
        for item_idx, item in enumerate(parts_list):
            model = item["model"]
            brand = item["brand"]
            quantity = item["quantity"]
            
            logger.info(f"[{item_idx+1}/{len(parts_list)}] Processing model: {model}, quantity: {quantity}")
            
            for site_id, scraper in scrapers.items():
                current_step += 1
                cache_key = f"{model}_{site_id}_{quantity}"
                
                # Check if already cached
                if cache_key in progress_cache:
                    logger.info(f"Found cached result for {model} on {scraper.name}")
                    # Update quantity and time if needed, but load cached data
                    cached_result = progress_cache[cache_key]
                    all_results.append(cached_result)
                    
                    if progress_callback:
                        progress_callback(current_step, total_steps, f"加载缓存: {model} 在 {scraper.name}")
                    continue

                # Notify progress
                if progress_callback:
                    progress_callback(current_step, total_steps, f"正在抓取: {model} 在 {scraper.name}")
                
                # Run Scraper
                try:
                    result = scraper.execute(playwright, model, quantity)
                    # Supplement brand if empty from list input
                    if not result.get("品牌") and brand:
                        result["品牌"] = brand
                except Exception as run_err:
                    logger.error(f"Scraper error on {scraper.name} for {model}: {run_err}")
                    result = scraper.get_empty_result(model, quantity)
                    result["货期"] = f"抓取异常: {str(run_err)[:40]}"
                    if brand:
                        result["品牌"] = brand
                
                # Append to results & cache
                all_results.append(result)
                progress_cache[cache_key] = result
                save_progress_cache(progress_cache)
                
                # Random safety delay to prevent bot bans
                time.sleep(1.0)
                
    # Save final comparison excel
    logger.info(f"Saving compiled results to: {output_excel_path}")
    save_comparison_results(all_results, output_excel_path)
    
    # Optionally clear progress cache after a completely successful run
    # so next run starts clean (or we can preserve it)
    logger.info("Scraping and comparison complete!")
    return all_results

def clear_cache():
    """
    Utility to clear progress cache.
    """
    if os.path.exists(PROGRESS_CACHE_FILE):
        try:
            os.remove(PROGRESS_CACHE_FILE)
            logger.info("Progress cache cleared successfully.")
        except Exception as e:
            logger.warning(f"Failed to clear progress cache: {e}")

if __name__ == "__main__":
    # Command Line Usage: python main.py [input_excel] [output_excel]
    if len(sys.argv) < 2:
        print("Usage: python main.py <input_excel_path> [output_excel_path] [--clear-cache]")
        sys.exit(1)
        
    input_path = sys.argv[1]
    
    if "--clear-cache" in sys.argv:
        clear_cache()
        
    output_path = "比价结果汇总.xlsx"
    if len(sys.argv) >= 3 and not sys.argv[2].startswith("--"):
        output_path = sys.argv[2]
        
    try:
        run_compare_tool(input_path, output_path, headless=False)
    except Exception as e:
        print(f"Error executing compare tool: {e}")
