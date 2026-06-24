"""Async Playwright browser pool for concurrent page management."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)


class BrowserPool:
    """Manages a pool of Playwright browser pages with concurrency control."""

    def __init__(
        self,
        max_pages: int = 5,
        headless: bool = True,
        storage_state_path: str | None = None,
    ) -> None:
        self._max_pages = max_pages
        self._headless = headless
        self._storage_state_path = Path(storage_state_path) if storage_state_path else None
        self._semaphore = asyncio.Semaphore(max_pages)
        self._playwright = None
        self._browser: Browser | None = None

    @property
    def headless(self) -> bool:
        return self._headless

    async def start(self) -> None:
        pw = await async_playwright().start()
        self._playwright = pw
        self._browser = await pw.chromium.launch(
            headless=self._headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
            ],
        )
        logger.info(f"BrowserPool started: max_pages={self._max_pages}, headless={self._headless}")

    async def acquire_page(self) -> Page:
        """Acquire a new page (blocks if pool is full)."""
        await self._semaphore.acquire()
        if self._browser is None:
            await self.start()
        context_kwargs = {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }
        if self._storage_state_path and self._storage_state_path.exists():
            context_kwargs["storage_state"] = str(self._storage_state_path)
        context = await self._browser.new_context(
            **context_kwargs,
        )
        page = await context.new_page()
        # Apply basic stealth
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        """)
        return page

    async def release_page(self, page: Page) -> None:
        """Release a page back to the pool."""
        try:
            if self._storage_state_path:
                self._storage_state_path.parent.mkdir(parents=True, exist_ok=True)
                await page.context.storage_state(path=str(self._storage_state_path))
            await page.context.close()
        except Exception:
            pass
        self._semaphore.release()

    async def stop(self) -> None:
        """Shut down the browser pool."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("BrowserPool stopped.")
