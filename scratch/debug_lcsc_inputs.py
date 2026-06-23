import sys
import os
from playwright.sync_api import sync_playwright

# Setup paths
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "code"))

from utils.browser_manager import create_browser_context, init_page

def debug_lcsc_inputs():
    url = "https://item.szlcsc.com/61542.html"
    print(f"Inspecting inputs on LCSC page: {url}")
    with sync_playwright() as p:
        context = create_browser_context(p, headless=True)
        page = init_page(context)
        
        page.goto(url)
        page.wait_for_timeout(5000)
        
        # Print page title
        print(f"Page Title: {page.title()}")
        
        # Get all input elements
        inputs = page.locator("input").all()
        print(f"Total inputs: {len(inputs)}")
        for idx, inp in enumerate(inputs):
            try:
                outer_html = inp.evaluate("el => el.outerHTML")
                val = inp.evaluate("el => el.value")
                print(f"\n--- Input {idx+1} ---")
                print(f"HTML: {outer_html}")
                print(f"Value: '{val}'")
            except Exception as e:
                print(f"Error inspecting input {idx+1}: {e}")
                
        context.close()
        context.browser.close()

if __name__ == "__main__":
    debug_lcsc_inputs()
