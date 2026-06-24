"""小猫芯城 (cmalls.net) adapter — Playwright rendering."""

from __future__ import annotations

import re
import logging

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool
from src.models import PartResult

logger = logging.getLogger(__name__)


@AdapterRegistry.register("cmalls")
class CmallsAdapter(BrowserAdapter):
    """
    小猫芯城 adapter.

    Strategy: Navigate to search URL → Playwright renders SPA → extract data.
    Search URL format: https://www.cmalls.net/search/{keyword}.html
    Verified: Browser renders 238KB with model data visible.
    """

    def __init__(self, browser_pool: BrowserPool) -> None:
        super().__init__("小猫芯城", browser_pool)

    async def search_by_mpn(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            url = f"https://www.cmalls.net/search/{mpn}.html"
            await page.goto(url, timeout=45000)
            await page.wait_for_timeout(8000)

            content = await page.content()
            return self._parse_results(mpn, content, url)
        except Exception as e:
            logger.error(f"[小猫芯城] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    def _parse_results(self, mpn: str, html: str, url: str) -> PartResult:
        """Parse product data from rendered HTML."""
        mpn_norm = self._normalize_text(mpn)

        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        # Extract prices
        prices = re.findall(r'[￥¥$]\s*(\d+\.?\d*)', html)
        if not prices:
            # Try plain decimal numbers that look like prices
            prices = re.findall(r'(\d+\.\d{2,4})', html)

        price_values = [float(p) for p in prices if 0.0001 < float(p) < 100000]
        if not price_values:
            # Model found but no price — still useful
            return self.success_result(mpn, {
                "mpn": mpn,
                "product_url": url,
            })

        best_price = min(price_values)

        # Extract stock
        stock = None
        stock_match = re.search(r'(?:Stock|库存|现货)[：:\s]*(\d[\d,]*)', html, re.I)
        if stock_match:
            stock = self._to_int(stock_match.group(1))

        # Extract brand
        brand = None
        brand_match = re.search(r'(?:Brand|品牌|厂商)[：:\s]*([^<\s]{2,30})', html, re.I)
        if brand_match:
            brand = brand_match.group(1)

        return self.success_result(mpn, {
            "mpn": mpn,
            "brand": brand,
            "stock": stock,
            "price_unit": best_price,
            "product_url": url,
        })
