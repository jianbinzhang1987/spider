"""硬之城 (allchips.com) adapter — Playwright with captcha solving."""

from __future__ import annotations

import re
import logging
from typing import Any

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool
from src.models import PartResult

logger = logging.getLogger(__name__)


@AdapterRegistry.register("allchips")
class AllchipsAdapter(BrowserAdapter):
    """
    硬之城 adapter.

    Strategy: Playwright + captcha solving (云之锁 CaptchaButton).
    Captcha: cdn.yzcstatic.com, mode=20 (slider) / mode=10 (click).
    Requires: ddddocr for slider gap detection + trajectory simulation.
    """

    SEARCH_URL = "https://www.allchips.com/search"

    def __init__(self, browser_pool: BrowserPool) -> None:
        super().__init__("硬之城", browser_pool)

    async def search_by_mpn(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            url = f"{self.SEARCH_URL}?keyword={mpn}"
            await page.goto(url, timeout=25000)
            await page.wait_for_timeout(5000)

            content = await page.content()

            # Check for captcha
            if self._has_captcha(content):
                solved = await self._attempt_captcha(page)
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
        indicators = ["captchabutton", "yzcstatic", "验证", "captcha"]
        html_lower = html.lower()
        return any(ind in html_lower for ind in indicators)

    async def _attempt_captcha(self, page) -> bool:
        """
        Attempt to solve YunZhiSuo slider captcha.

        Flow:
        1. Click verification trigger button
        2. Wait for slider image to load
        3. Screenshot → ddddocr detect gap position
        4. Simulate human-like drag trajectory
        """
        try:
            # Try to find and click the captcha trigger
            trigger = await page.query_selector(
                'a[onclick*="showCap"], .captcha-trigger, [class*="captcha"] button'
            )
            if trigger:
                await trigger.click()
                await page.wait_for_timeout(2000)

            # Try to detect and solve slider
            slider = await page.query_selector(
                '.slider-btn, .captcha-slider, [class*="slide"]'
            )
            if not slider:
                logger.warning("[硬之城] Slider element not found")
                return False

            # For now, return False — full implementation requires:
            # 1. Screenshot the captcha background image
            # 2. Use ddddocr.slide_match() to find gap position
            # 3. Generate human-like mouse trajectory
            # 4. Perform drag action
            logger.info("[硬之城] Captcha detected — solver not yet implemented")
            return False

        except Exception as e:
            logger.warning(f"[硬之城] Captcha solving error: {e}")
            return False

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
