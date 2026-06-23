import os
import logging
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth

logger = logging.getLogger(__name__)

# Default user agent to mimic normal desktop Chrome
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def get_session_path(site_name: str) -> str:
    """
    Get the path for persisting session state (Cookies/LocalStorage) for a specific site.
    """
    sessions_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sessions")
    os.makedirs(sessions_dir, exist_ok=True)
    return os.path.join(sessions_dir, f"{site_name}_state.json")

def create_browser_context(
    playwright_instance, 
    headless: bool = False, 
    state_path: str = None
) -> BrowserContext:
    """
    Launch a Chromium browser and create a context with stealth configurations.
    """
    # Look for local Chrome to avoid dependency download issues
    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    launch_args = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-infobars",
            "--window-position=0,0",
            "--ignore-certificate-errors"
        ]
    }
    if os.path.exists(chrome_path):
        logger.info(f"Using local system Google Chrome: {chrome_path}")
        launch_args["executable_path"] = chrome_path
    else:
        logger.info("Local Google Chrome not found, using default Playwright Chromium.")

    # Launch browser
    browser = playwright_instance.chromium.launch(**launch_args)
    
    # Configure context arguments
    context_args = {
        "user_agent": DEFAULT_USER_AGENT,
        "viewport": {"width": 1280, "height": 800},
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai"
    }
    
    # Load session state if exists
    if state_path and os.path.exists(state_path):
        logger.info(f"Loading session state from: {state_path}")
        context_args["storage_state"] = state_path
        
    context = browser.new_context(**context_args)
    
    return context

def init_page(context: BrowserContext) -> Page:
    """
    Create a new page in the context and apply stealth scripts to bypass detection.
    """
    page = context.new_page()
    # Apply playwright-stealth to the page
    try:
        Stealth().apply_stealth_sync(page)
    except Exception as e:
        logger.warning(f"Failed to apply playwright-stealth: {e}")
        
    return page
