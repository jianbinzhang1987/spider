"""艾汐芯城 (ic-stk.cn) adapter — Playwright rendering."""

from __future__ import annotations

import re
import logging

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool
from src.models import PartResult

logger = logging.getLogger(__name__)


@AdapterRegistry.register("icstk")
class IcstkAdapter(BrowserAdapter):
    """
    艾汐芯城 adapter.

    Strategy: Playwright renders page (bypasses WAF) → extract product data.
    Verified: Browser renders homepage (109KB), WAF bypassed.
    Note: May require China IP for full product data.
    """

    def __init__(self, browser_pool: BrowserPool) -> None:
        super().__init__("艾汐芯城", browser_pool)

    async def search_by_mpn(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            url = f"https://www.ic-stk.cn/search?keyword={mpn}"
            await page.goto(url, timeout=30000)
            await page.wait_for_timeout(8000)

            content = await page.content()
            return self._parse_results(mpn, content, url)
        except Exception as e:
            logger.error(f"[艾汐芯城] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

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

        result_data = {
            "mpn": mpn,
            "brand": brand,
            "stock": stock,
            "product_url": url,
        }

        if price_values:
            result_data["price_unit"] = min(price_values)

        return self.success_result(mpn, result_data)
