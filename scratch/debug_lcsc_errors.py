import sys
import os
from playwright.sync_api import sync_playwright

# Setup paths
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "code"))

from utils.browser_manager import create_browser_context, init_page

def debug_lcsc_errors():
    url = "https://item.szlcsc.com/61542.html"
    print(f"Inspecting console errors on LCSC page: {url}")
    with sync_playwright() as p:
        context = create_browser_context(p, headless=False)
        page = init_page(context)
        
        # Listen to console and pageerror
        page.on("console", lambda msg: print(f"[Console] {msg.type}: {msg.text}"))
        page.on("pageerror", lambda err: print(f"[PageError] {err}"))
        
        page.goto(url)
        page.wait_for_timeout(5000)
        
        context.close()
        context.browser.close()

if __name__ == "__main__":
    debug_lcsc_errors()
