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

    SEARCH_URL = "https://www.wlxmall.com/search/1.html"

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
            try:
                body_text = await page.locator("body").inner_text(timeout=5000)
            except Exception:
                body_text = ""
            return self._parse_results(mpn, f"{content}\n{body_text}", url)
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
            return self.success_result(mpn, {
                "mpn": mpn,
                "brand": self._extract_brand(html),
                "stock": self._extract_stock(html),
                "product_url": url,
            })

        price_values = [float(p) for p in prices if 0.0001 < float(p) < 100000]
        if not price_values:
            return self.success_result(mpn, {
                "mpn": mpn,
                "brand": self._extract_brand(html),
                "stock": self._extract_stock(html),
                "product_url": url,
                "lead_time": self._extract_lead_time(html),
            })

        best_price = min(price_values)

        # Extract stock info
        stock = self._extract_stock(html)

        # Extract brand
        brand = self._extract_brand(html)

        return self.success_result(mpn, {
            "mpn": mpn,
            "brand": brand,
            "stock": stock,
            "price_unit": best_price,
            "product_url": url,
            "lead_time": self._extract_lead_time(html),
        })

    def _extract_stock(self, html: str) -> int | None:
        stock_match = re.search(r'自营现货库存[：:\s]*(\d[\d,]*)', html)
        if stock_match:
            return self._to_int(stock_match.group(1))
        stock_match = re.search(r'(?:库存)[：:\s]*(\d[\d,]*)', html)
        return self._to_int(stock_match.group(1)) if stock_match else None

    def _extract_brand(self, html: str) -> str | None:
        text = re.sub(r"<[^>]+>", " ", html)
        brand_match = re.search(r'(?:品牌|厂商)[：:]\s*([A-Za-z0-9\u4e00-\u9fff（）()/ .,&\-]{2,40})', text)
        if not brand_match:
            return None
        brand = re.sub(r"\s+", " ", brand_match.group(1)).strip()
        return brand or None

    def _extract_lead_time(self, html: str) -> str | None:
        lead_match = re.search(r'货期[：:\s]*([^\s<|]{2,20})', html)
        return lead_match.group(1) if lead_match else None
