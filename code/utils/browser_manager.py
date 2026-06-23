import os
import sys
import shutil
import logging
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth

logger = logging.getLogger(__name__)

# Default user agent to mimic normal desktop Chrome
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

def _find_system_chrome() -> str:
    """
    Cross-platform detection of a locally installed Chrome/Chromium binary.
    Returns the path if found, otherwise an empty string (Playwright Chromium
    will be used as the fallback).
    """
    candidates = []
    if sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif sys.platform.startswith("linux"):
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/snap/bin/chromium",
        ]
        which_chrome = shutil.which("google-chrome") or shutil.which("chromium-browser") or shutil.which("chromium")
        if which_chrome:
            candidates.insert(0, which_chrome)
    elif sys.platform == "win32":
        prog = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        prog86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            os.path.join(prog, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(prog86, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
        ]

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


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
    state_path: str = None,
    existing_browser: Browser = None
) -> BrowserContext:
    """
    Create a browser context with stealth configurations.

    If *existing_browser* is provided the context is created on that browser
    instance (no new browser process is launched).  Otherwise a new Chromium
    browser is started – preferring the system Chrome when available.
    """
    browser = existing_browser
    if browser is None:
        chrome_path = _find_system_chrome()
        launch_args = {
            "headless": headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--window-position=0,0",
                "--ignore-certificate-errors",
                "--disable-dev-shm-usage",
            ],
        }
        if chrome_path:
            logger.info(f"Using local system Chrome: {chrome_path}")
            launch_args["executable_path"] = chrome_path
        else:
            logger.info("System Chrome not found – using Playwright Chromium.")

        browser = playwright_instance.chromium.launch(**launch_args)

    # Configure context arguments
    context_args = {
        "user_agent": DEFAULT_USER_AGENT,
        "viewport": {"width": 1280, "height": 800},
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
    }

    # Load session state if exists
    if state_path and os.path.exists(state_path):
        logger.info(f"Loading session state from: {state_path}")
        context_args["storage_state"] = state_path

    context = browser.new_context(**context_args)
    return context


def launch_browser(playwright_instance, headless: bool = False) -> Browser:
    """
    Launch a single Chromium browser instance that can be reused across
    multiple scraper contexts.
    """
    chrome_path = _find_system_chrome()
    launch_args = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-infobars",
            "--window-position=0,0",
            "--ignore-certificate-errors",
            "--disable-dev-shm-usage",
        ],
    }
    if chrome_path:
        logger.info(f"Using local system Chrome: {chrome_path}")
        launch_args["executable_path"] = chrome_path
    else:
        logger.info("System Chrome not found – using Playwright Chromium.")

    return playwright_instance.chromium.launch(**launch_args)


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
