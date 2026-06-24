"""唯样商城 (oneyac.com) adapter — curl_cffi with TLS impersonation."""

from __future__ import annotations

import json
import re
import logging
from typing import Any

from src.adapters.base import HttpAdapter
from src.adapters.registry import AdapterRegistry
from src.models import PartResult, PriceBreak

logger = logging.getLogger(__name__)


@AdapterRegistry.register("oneyac")
class OneyacAdapter(HttpAdapter):
    """
    唯样商城 adapter.

    Strategy: curl_cffi bypasses TLS fingerprint detection → fetch search page HTML
              → extract embedded product data from server-rendered content.
    Verified: curl_cffi with chrome impersonation successfully retrieves 90KB HTML
              containing model numbers.
    Note: Full price data requires soic.oneyac.com JSONP API with client-generated
          token (JS reverse engineering needed for complete pricing).
    """

    SEARCH_URL = "https://www.oneyac.com/search.html"

    def __init__(self, timeout: float = 15.0, min_interval: float = 1.5) -> None:
        super().__init__("唯样商城", timeout=timeout, min_interval=min_interval)

    async def search_by_mpn(self, mpn: str) -> PartResult:
        try:
            response = await self._fetch(self.SEARCH_URL, params={"keyword": mpn})

            if response.status_code != 200:
                return self.failed_result(mpn, f"HTTP {response.status_code}")

            html = response.text
            if len(html) < 1000:
                return self.failed_result(mpn, "Response too small, possibly blocked")

            return self._parse_html(mpn, html)
        except Exception as e:
            logger.error(f"[唯样商城] search failed: {e}")
            return self.failed_result(mpn, str(e))

    def _parse_html(self, mpn: str, html: str) -> PartResult:
        """Parse search results from server-rendered HTML."""
        mpn_norm = self._normalize_text(mpn)

        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        # Try to extract embedded JSON data (oneyac often embeds initial state)
        json_data = self._extract_embedded_json(html)
        if json_data:
            return self._parse_json_data(mpn, json_data)

        # Fallback: regex extraction from HTML
        return self._parse_html_regex(mpn, html)

    def _extract_embedded_json(self, html: str) -> dict | None:
        """Look for embedded JSON product data in script tags."""
        patterns = [
            r'window\.__INITIAL_STATE__\s*=\s*({.+?});?\s*</script>',
            r'var\s+searchResult\s*=\s*({.+?});?\s*</script>',
            r'data-products=["\']({.+?})["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.S)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
        return None

    def _parse_json_data(self, mpn: str, data: dict) -> PartResult:
        """Parse product data from embedded JSON."""
        # Navigate through common JSON structures
        products = None
        for key in ("products", "list", "data", "items", "content", "result"):
            if key in data:
                val = data[key]
                if isinstance(val, list):
                    products = val
                    break
                elif isinstance(val, dict):
                    for sub_key in ("list", "items", "products"):
                        if sub_key in val and isinstance(val[sub_key], list):
                            products = val[sub_key]
                            break
                if products:
                    break

        if not products:
            return self.not_found_result(mpn)

        # Find the best matching product
        mpn_norm = self._normalize_text(mpn)
        best = None
        for product in products:
            if not isinstance(product, dict):
                continue
            for field in ("partNo", "mpn", "model", "partNumber", "goods_name"):
                val = product.get(field, "")
                if val and mpn_norm in self._normalize_text(str(val)):
                    best = product
                    break
            if best:
                break

        if not best:
            best = products[0] if products else None

        if not best or not isinstance(best, dict):
            return self.not_found_result(mpn)

        return self.success_result(mpn, {
            "mpn": best.get("partNo") or best.get("mpn") or best.get("model") or mpn,
            "brand": best.get("brand") or best.get("manufacturer") or best.get("brandName"),
            "stock": best.get("stock") or best.get("inventory") or best.get("stockQty"),
            "price_unit": best.get("price") or best.get("unitPrice") or best.get("minPrice"),
            "moq": best.get("moq") or best.get("minQty"),
            "package": best.get("package") or best.get("encapsulation"),
            "description": best.get("description") or best.get("desc"),
            "product_url": f"https://www.oneyac.com/search.html?keyword={mpn}",
        })

    def _parse_html_regex(self, mpn: str, html: str) -> PartResult:
        """Fallback: extract data via regex from HTML."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")

        # Extract prices — multiple patterns
        price_values: list[float] = []

        # Pattern 1: ¥/￥ followed by numbers
        prices = re.findall(r'[￥¥]\s*(\d+\.?\d*)', html)
        price_values.extend(float(p) for p in prices if 0.0001 < float(p) < 100000)

        # Pattern 2: class containing "price" with numeric content
        for el in soup.select('[class*="price"], [class*="Price"]'):
            text = el.get_text(strip=True)
            nums = re.findall(r'(\d+\.?\d+)', text)
            for n in nums:
                v = float(n)
                if 0.0001 < v < 100000:
                    price_values.append(v)

        # Pattern 3: data attributes containing price
        for el in soup.select('[data-price], [data-min-price]'):
            for attr in ("data-price", "data-min-price", "data-unit-price"):
                val = el.get(attr)
                if val:
                    try:
                        v = float(val)
                        if 0.0001 < v < 100000:
                            price_values.append(v)
                    except (ValueError, TypeError):
                        pass

        # Extract brand
        brand = None
        brand_match = re.search(r'(?:品牌|brand|厂商)[：:\s]*([^<\s]{2,30})', html, re.I)
        if brand_match:
            brand = brand_match.group(1)
        if not brand:
            for el in soup.select('[class*="brand"], [class*="Brand"], [class*="mfr"]'):
                text = el.get_text(strip=True)
                if text and 2 <= len(text) <= 30:
                    brand = text
                    break

        # Extract stock
        stock = None
        stock_match = re.search(r'(?:库存|stock|现货)[：:\s]*([\d,]+)', html, re.I)
        if stock_match:
            stock = self._to_int(stock_match.group(1))
        if not stock:
            for el in soup.select('[class*="stock"], [class*="inventory"]'):
                text = el.get_text(strip=True)
                nums = re.findall(r'([\d,]+)', text)
                if nums:
                    stock = self._to_int(nums[0])
                    break

        result_data: dict[str, Any] = {
            "mpn": mpn,
            "brand": brand,
            "stock": stock,
            "product_url": f"https://www.oneyac.com/search.html?keyword={mpn}",
        }

        if price_values:
            result_data["price_unit"] = min(price_values)

        return self.success_result(mpn, result_data)
