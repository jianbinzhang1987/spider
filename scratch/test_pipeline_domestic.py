import sys
import os
import json
import logging
from playwright.sync_api import sync_playwright

# Setup paths
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "code"))

from scrapers.domestic.szlcsc import SzlcscScraper
from scrapers.domestic.ickey import IckeyScraper
from scrapers.domestic.hqew import HqewScraper

# Configure logging to stdout
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def test_pipeline():
    model = "RC0402FR-0710KL"
    quantity = 50000
    print(f"\n==========================================")
    print(f"Testing domestic scrapers for Model: {model}, Qty: {quantity}")
    print(f"==========================================\n")
    
    scrapers = {
        "LCSC (立创商城)": SzlcscScraper(headless=False),
        "Ickey (云汉芯城)": IckeyScraper(headless=False),
        "HQEW (华强电子网)": HqewScraper(headless=False)
    }
    
    results = {}
    with sync_playwright() as playwright:
        for name, scraper in scrapers.items():
            try:
                print(f"--- Running {name} Scraper ---")
                res = scraper.execute(playwright, model, quantity)
                results[name] = res
                print(f"Result for {name}:")
                print(json.dumps(res, ensure_ascii=False, indent=4))
                print("-" * 50)
            except Exception as e:
                print(f"Error running {name}: {e}")
                print("-" * 50)
                
    print("\n================ SUMMARY ================")
    for name, res in results.items():
        price = res.get("适用价格(人民币)")
        stock = res.get("库存数量")
        lead = res.get("货期")
        link = res.get("渠道链接")
        print(f"{name}: Price = {price} CNY | Stock = {stock} | LeadTime = {lead} | Link = {link}")
    print("=========================================")

if __name__ == "__main__":
    test_pipeline()
