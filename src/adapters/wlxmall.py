"""万联芯城 (wlxmall.com) adapter — Playwright with AJAX wait."""

from __future__ import annotations

import re
import logging
from typing import Any

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool
from src.models import PartResult, PriceBreak

logger = logging.getLogger(__name__)


@AdapterRegistry.register("wlxmall")
class WlxmallAdapter(BrowserAdapter):
    """
    万联芯城 adapter.

    Strategy: Playwright renders SSR page → waits for jQuery AJAX to load products.
    Known AJAX: POST /post/goods.item_search:search with {conditions, page, size}.
    Verified: Browser renders full product data with model + prices.
    """

    SEARCH_URL = "https://www.wlxmall.com/search"

    def __init__(self, browser_pool: BrowserPool) -> None:
        super().__init__("万联芯城", browser_pool)

    async def search_by_mpn(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            url = f"{self.SEARCH_URL}?keywords={mpn}"
            await page.goto(url, timeout=25000)
            # Wait for AJAX product data to load
            await page.wait_for_timeout(10000)

            content = await page.content()
            return self._parse_results(mpn, content, url)
        except Exception as e:
            logger.error(f"[万联芯城] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    def _parse_results(self, mpn: str, html: str, url: str) -> PartResult:
        """Parse product data from rendered HTML."""
        mpn_norm = self._normalize_text(mpn)

        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        # Extract prices (¥ format)
        prices = re.findall(r'[￥¥]\s*(\d+\.?\d*)', html)
        if not prices:
            return self.not_found_result(mpn)

        price_values = [float(p) for p in prices if 0.0001 < float(p) < 100000]
        if not price_values:
            return self.not_found_result(mpn)

        best_price = min(price_values)

        # Extract stock info
        stock = None
        stock_match = re.search(r'(?:库存|现货)[：:\s]*(\d[\d,]*)', html)
        if stock_match:
            stock = self._to_int(stock_match.group(1))

        # Extract brand
        brand = None
        brand_match = re.search(r'(?:品牌|厂商)[：:\s]*([^<\s]{2,30})', html)
        if brand_match:
            brand = brand_match.group(1)

        return self.success_result(mpn, {
            "mpn": mpn,
            "brand": brand,
            "stock": stock,
            "price_unit": best_price,
            "product_url": url,
        })
