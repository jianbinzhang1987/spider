"""华强电子网 (hqew.com) adapter — Playwright direct rendering."""

from __future__ import annotations

import re
import logging
from typing import Any

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool
from src.models import PartResult, QueryStatus

logger = logging.getLogger(__name__)


@AdapterRegistry.register("hqew")
class HqewAdapter(BrowserAdapter):
    """
    华强电子网 adapter.

    Strategy: Playwright renders search page → extract product table.
    Verified: https://s.hqew.com/?cid=0&q={keyword} renders with model + prices.
    """

    SEARCH_URL = "https://s.hqew.com/"

    def __init__(self, browser_pool: BrowserPool) -> None:
        super().__init__("华强电子网", browser_pool)

    async def search_by_mpn(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            url = f"{self.SEARCH_URL}?cid=0&q={mpn}"
            await page.goto(url, timeout=25000)
            await page.wait_for_timeout(8000)

            content = await page.content()
            return self._parse_results(mpn, content, url)
        except Exception as e:
            logger.error(f"[华强电子网] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    def _parse_results(self, mpn: str, html: str, url: str) -> PartResult:
        """Parse search results from rendered HTML."""
        mpn_norm = self._normalize_text(mpn)

        # Extract prices from the page
        prices = re.findall(r'[￥¥]\s*(\d+\.?\d*)', html)
        if not prices:
            prices = re.findall(r'(\d+\.\d{2,4})', html)

        # Check if model number is present
        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        if not prices:
            return self.not_found_result(mpn)

        # Get the lowest price
        price_values = [float(p) for p in prices if 0.0001 < float(p) < 100000]
        if not price_values:
            return self.not_found_result(mpn)

        best_price = min(price_values)

        # Try to extract brand
        brand = self._extract_brand(html, mpn)

        # Try to extract stock
        stock = self._extract_stock(html)

        return self.success_result(mpn, {
            "mpn": mpn,
            "brand": brand,
            "stock": stock,
            "price_unit": best_price,
            "product_url": url,
        })

    def _extract_brand(self, html: str, mpn: str) -> str | None:
        """Try to extract brand name near the model number."""
        # Common pattern: brand/中文名
        brands = re.findall(r'([A-Z][A-Za-z]+)/([^\s<"]{2,8})', html)
        if brands:
            return f"{brands[0][0]}/{brands[0][1]}"
        # Single brand name
        brand_match = re.search(
            r'(?:品牌|brand)[：:]\s*([^<\s]{2,20})',
            html, re.I
        )
        return brand_match.group(1) if brand_match else None

    def _extract_stock(self, html: str) -> int | None:
        """Try to extract stock quantity."""
        stock_match = re.search(r'(?:库存|stock)[：:]\s*([\d,]+)', html, re.I)
        if stock_match:
            return self._to_int(stock_match.group(1))
        return None
