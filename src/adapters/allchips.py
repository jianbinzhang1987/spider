"""硬之城 (allchips.com) adapter — Playwright with captcha solving."""

from __future__ import annotations

import re
import logging
from typing import Any

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool
from src.core.captcha_solver import CaptchaSolver
from src.models import PartResult

logger = logging.getLogger(__name__)

# YunZhiSuo (云之锁) captcha selectors
YZS_TRIGGER_SELECTORS = [
    'a[onclick*="showCap"]',
    '.captcha-trigger',
    '[class*="captcha"] button',
    '#captchaBtn',
    '.yzsm_btn',
]
YZS_BG_SELECTORS = [
    '.yzsm_bg img',
    '.captcha-bg img',
    '[class*="captcha-bg"] img',
    'canvas.captcha-canvas',
]
YZS_SLIDE_SELECTORS = [
    '.yzsm_slide_btn',
    '.slider-btn',
    '.captcha-slider-btn',
    '[class*="slide"] .btn',
    '.yzsm_drag_btn',
]
YZS_PIECE_SELECTORS = [
    '.yzsm_slide_piece img',
    '.captcha-piece img',
    '.slide-piece',
]


@AdapterRegistry.register("allchips")
class AllchipsAdapter(BrowserAdapter):
    """
    硬之城 adapter.

    Strategy: Playwright + captcha solving (云之锁 CaptchaButton).
    Captcha: cdn.yzcstatic.com, mode=20 (slider) / mode=10 (click).
    Uses ddddocr for slider gap detection + human-like trajectory simulation.
    """

    SEARCH_URL = "https://www.allchips.com/search"
    MAX_CAPTCHA_RETRIES = 3

    def __init__(self, browser_pool: BrowserPool) -> None:
        super().__init__("硬之城", browser_pool)
        self._solver = CaptchaSolver()

    async def search_by_mpn(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            url = f"{self.SEARCH_URL}?keyword={mpn}"
            await page.goto(url, timeout=25000)
            await page.wait_for_timeout(5000)

            content = await page.content()

            # Check for captcha and attempt to solve
            if self._has_captcha(content):
                solved = await self._solve_with_retries(page)
                if not solved:
                    return self.failed_result(mpn, "验证码未通过(云之锁)")
                await page.wait_for_timeout(5000)
                content = await page.content()

            return self._parse_results(mpn, content, url)
        except Exception as e:
            logger.error(f"[硬之城] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    def _has_captcha(self, html: str) -> bool:
        """Detect YunZhiSuo captcha presence."""
        indicators = ["captchabutton", "yzcstatic", "验证码", "captcha", "yzsm_"]
        html_lower = html.lower()
        return any(ind in html_lower for ind in indicators)

    async def _solve_with_retries(self, page) -> bool:
        """Attempt captcha solving with retries."""
        for attempt in range(1, self.MAX_CAPTCHA_RETRIES + 1):
            logger.info(f"[硬之城] Captcha attempt {attempt}/{self.MAX_CAPTCHA_RETRIES}")
            solved = await self._attempt_captcha(page)
            if solved:
                return True
            await page.wait_for_timeout(1000)
        return False

    async def _attempt_captcha(self, page) -> bool:
        """
        Attempt to solve YunZhiSuo slider captcha.

        Flow:
        1. Click verification trigger button to show captcha panel
        2. Wait for captcha images to load
        3. Screenshot background + slider piece
        4. Use ddddocr.slide_match() to detect gap position
        5. Generate human-like drag trajectory
        6. Perform drag action
        """
        try:
            # Step 1: Click trigger to show captcha panel
            trigger = await self._find_element(page, YZS_TRIGGER_SELECTORS)
            if trigger:
                await trigger.click()
                await page.wait_for_timeout(2000)

            # Step 2: Get the background image
            bg_element = await self._find_element(page, YZS_BG_SELECTORS)
            if not bg_element:
                logger.warning("[硬之城] Background image not found")
                return False

            # Step 3: Get slider piece image
            piece_element = await self._find_element(page, YZS_PIECE_SELECTORS)

            # Step 4: Get slider button
            slider_btn = await self._find_element(page, YZS_SLIDE_SELECTORS)
            if not slider_btn:
                logger.warning("[硬之城] Slider button not found")
                return False

            # Step 5: Screenshot and detect gap
            bg_bytes = await bg_element.screenshot()
            piece_bytes = await piece_element.screenshot() if piece_element else None

            if piece_bytes:
                gap_x = self._solver.detect_slide_gap(bg_bytes, piece_bytes)
            else:
                gap_x = self._solver._detect_gap_from_bg(bg_bytes)

            if gap_x <= 10:
                logger.warning(f"[硬之城] Invalid gap position: {gap_x}")
                return False

            logger.info(f"[硬之城] Detected gap at x={gap_x}")

            # Step 6: Perform drag with human-like trajectory
            btn_box = await slider_btn.bounding_box()
            if not btn_box:
                return False

            start_x = btn_box["x"] + btn_box["width"] / 2
            start_y = btn_box["y"] + btn_box["height"] / 2

            trajectory = self._solver.generate_trajectory(gap_x)

            await page.mouse.move(start_x, start_y)
            await page.mouse.down()

            import asyncio
            import random
            await asyncio.sleep(random.uniform(0.08, 0.15))

            for x_offset, y_offset, dt in trajectory:
                await page.mouse.move(start_x + x_offset, start_y + y_offset)
                await asyncio.sleep(dt / 1000.0)

            await asyncio.sleep(random.uniform(0.05, 0.1))
            await page.mouse.up()

            # Step 7: Verify result
            await page.wait_for_timeout(2000)
            content = await page.content()

            if not self._has_captcha(content):
                logger.info("[硬之城] Captcha solved successfully")
                return True

            # Check for explicit success/failure indicators
            success_texts = ["验证成功", "通过验证"]
            failure_texts = ["验证失败", "请重试"]
            for text in success_texts:
                if text in content:
                    return True
            for text in failure_texts:
                if text in content:
                    logger.info(f"[硬之城] Captcha failed: {text}")
                    return False

            return False

        except Exception as e:
            logger.warning(f"[硬之城] Captcha solving error: {e}")
            return False

    async def _find_element(self, page, selectors: list[str]):
        """Try multiple selectors to find an element."""
        for sel in selectors:
            try:
                elem = await page.query_selector(sel)
                if elem:
                    visible = await elem.is_visible()
                    if visible:
                        return elem
            except Exception:
                pass
        return None

    def _parse_results(self, mpn: str, html: str, url: str) -> PartResult:
        """Parse product data from rendered HTML."""
        mpn_norm = self._normalize_text(mpn)

        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        prices = re.findall(r'[￥¥$]\s*(\d+\.?\d*)', html)
        price_values = [float(p) for p in prices if 0.0001 < float(p) < 100000]

        stock = None
        stock_match = re.search(r'(?:库存|stock|现货)[：:\s]*(\d[\d,]*)', html, re.I)
        if stock_match:
            stock = self._to_int(stock_match.group(1))

        brand = None
        brand_match = re.search(r'(?:品牌|brand|厂商)[：:\s]*([^<\s]{2,30})', html, re.I)
        if brand_match:
            brand = brand_match.group(1)

        result_data: dict[str, Any] = {
            "mpn": mpn,
            "brand": brand,
            "stock": stock,
            "product_url": url,
        }

        if price_values:
            result_data["price_unit"] = min(price_values)

        return self.success_result(mpn, result_data)
