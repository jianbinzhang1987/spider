"""唯样商城 (oneyac.com) adapter — Playwright with API interception.

Strategy: Use Playwright to load search page → intercept JSONP/API calls that
          fetch price data → parse rendered DOM as fallback.
Note: Prices are loaded asynchronously via soic.oneyac.com JSONP with client-generated
      token. Curl_cffi can only get the initial HTML which contains the MPN but no prices.
"""

from __future__ import annotations

import json
import re
import logging
from typing import Any

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool
from src.models import PartResult

logger = logging.getLogger(__name__)


@AdapterRegistry.register("oneyac")
class OneyacAdapter(BrowserAdapter):
    """
    唯样商城 adapter using Playwright browser.

    Prices are loaded via async API calls (soic.oneyac.com) which require
    browser-side JS execution. We intercept these responses for reliable data.
    """

    SEARCH_URL = "https://www.oneyac.com/search.html"

    def __init__(self, browser_pool: BrowserPool) -> None:
        super().__init__("唯样商城", browser_pool)

    async def search_by_mpn(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            api_data: dict[str, Any] = {}

            async def capture_response(response):
                url = response.url
                # Capture price/product API responses
                if "soic.oneyac.com" in url or "api.oneyac.com" in url or "oneyac.com/api" in url:
                    try:
                        text = await response.text()
                        if not text:
                            return
                        # Handle JSONP: strip callback wrapper
                        cleaned = text.strip()
                        if cleaned.startswith("(") or "callback" in url:
                            # Try stripping JSONP callback: funcName({...})
                            match = re.search(r'\((\{.+\})\)', cleaned, re.S)
                            if match:
                                cleaned = match.group(1)
                        if cleaned.startswith("{") or cleaned.startswith("["):
                            data = json.loads(cleaned)
                            if "price" not in api_data:
                                api_data["price"] = []
                            api_data["price"].append(data)
                    except Exception:
                        pass

            page.on("response", capture_response)
            url = f"{self.SEARCH_URL}?keyword={mpn}"
            await page.goto(url, timeout=30000)
            await page.wait_for_timeout(8000)

            # Check if MPN found in page
            content = await page.content()
            mpn_norm = self._normalize_text(mpn)
            if mpn_norm not in self._normalize_text(content):
                return self.not_found_result(mpn)

            # Try to extract prices from intercepted API data
            price = self._extract_price_from_api(mpn, api_data)

            # Fallback: extract prices from rendered DOM
            if price is None:
                price = self._extract_price_from_dom(content)

            result_data: dict[str, Any] = {
                "mpn": mpn,
                "product_url": url,
            }

            if price is not None:
                result_data["price_unit"] = price

            # Extract brand from DOM
            brand = self._extract_brand(content)
            if brand:
                result_data["brand"] = brand

            # Extract stock from DOM
            stock = self._extract_stock(content)
            if stock:
                result_data["stock"] = stock

            return self.success_result(mpn, result_data)
        except Exception as e:
            logger.error(f"[唯样商城] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    def _extract_price_from_api(self, mpn: str, api_data: dict[str, Any]) -> float | None:
        """Extract price from intercepted API responses."""
        price_responses = api_data.get("price", [])
        if not price_responses:
            return None

        prices: list[float] = []
        for data in price_responses:
            self._scan_for_prices(data, prices)

        return min(prices) if prices else None

    def _scan_for_prices(self, obj: Any, prices: list[float], depth: int = 0) -> None:
        """Recursively scan JSON for price values."""
        if depth > 5:
            return
        if isinstance(obj, dict):
            for key, val in obj.items():
                key_lower = key.lower()
                if any(p in key_lower for p in ("price", "unit_price", "unitprice", "sell")):
                    if isinstance(val, (int, float)) and 0.0001 < val < 100000:
                        prices.append(float(val))
                    elif isinstance(val, str):
                        try:
                            v = float(re.sub(r'[^\d.]', '', val))
                            if 0.0001 < v < 100000:
                                prices.append(v)
                        except (ValueError, TypeError):
                            pass
                self._scan_for_prices(val, prices, depth + 1)
        elif isinstance(obj, list):
            for item in obj[:20]:
                self._scan_for_prices(item, prices, depth + 1)

    def _extract_price_from_dom(self, html: str) -> float | None:
        """Extract price from rendered DOM content."""
        price_values: list[float] = []

        # Pattern 1: Currency symbol ¥/￥ followed by numbers
        prices = re.findall(r'[￥¥]\s*(\d+\.?\d*)', html)
        price_values.extend(float(p) for p in prices if 0.0001 < float(p) < 100000)

        # Pattern 2: Price in data attributes or embedded JSON
        data_prices = re.findall(r'"(?:price|unitPrice|unit_price|sell_price|minPrice)"[:\s]*["\']?(\d+\.?\d*)', html)
        price_values.extend(float(p) for p in data_prices if 0.0001 < float(p) < 100000)

        # Pattern 3: Ladder price (e.g., "1+ ¥0.05" or "1+  0.05")
        ladder_prices = re.findall(r'\d+\+\s*[￥¥]?\s*(\d+\.?\d+)', html)
        price_values.extend(float(p) for p in ladder_prices if 0.0001 < float(p) < 100000)

        # Pattern 4: class="price" elements (check text content for numbers)
        price_spans = re.findall(r'class="[^"]*price[^"]*"[^>]*>([^<]+)', html, re.I)
        for text in price_spans:
            nums = re.findall(r'(\d+\.?\d+)', text)
            for n in nums:
                v = float(n)
                if 0.0001 < v < 100000:
                    price_values.append(v)

        return min(price_values) if price_values else None

    def _extract_brand(self, html: str) -> str | None:
        """Extract brand/manufacturer from DOM."""
        brand_match = re.search(r'(?:品牌|brand|厂商|制造商)[：:\s]*([^<\s]{2,30})', html, re.I)
        if brand_match:
            return brand_match.group(1).strip()
        return None

    def _extract_stock(self, html: str) -> int | None:
        """Extract stock quantity from DOM."""
        stock_match = re.search(r'(?:库存|stock|现货)[：:\s]*([\d,]+)', html, re.I)
        if stock_match:
            return self._to_int(stock_match.group(1))
        return None
