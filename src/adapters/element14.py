"""element14/Farnell/Newark adapter — Official Product Search API (REST).

Authentication: API Key (query parameter).
Endpoint: https://api.element14.com/catalog/products
Requires: ELEMENT14_API_KEY environment variable.
Optional: ELEMENT14_STORE (default: "cn.element14.com")

Docs: https://partner.element14.com/docs
"""

from __future__ import annotations

import logging
import re
from typing import Any
from html import unescape

from src.adapters.base import HttpAdapter
from src.adapters.registry import AdapterRegistry
from src.config import get
from src.models import PartResult

logger = logging.getLogger(__name__)

API_URL = "https://api.element14.com/catalog/products"


@AdapterRegistry.register("element14")
class Element14Adapter(HttpAdapter):
    """element14/Farnell adapter using official Product Search API."""

    def __init__(self) -> None:
        super().__init__("element14", timeout=20.0, min_interval=0.5)
        self._api_key = get("element14.api_key")
        self._store = get("element14.store") or "cn.element14.com"

    async def search_by_mpn(self, mpn: str) -> PartResult:
        if not self._api_key:
            return await self._search_via_web(mpn)

        try:
            client = self._get_client()
            resp = await client.get(
                API_URL,
                params={
                    "term": f"mfpSearch:{mpn}",
                    "storeInfo.id": self._store,
                    "resultsSettings.offset": 0,
                    "resultsSettings.numberOfResults": 10,
                    "resultsSettings.responseGroup": "large",
                    "callInfo.responseDataFormat": "json",
                    "callinfo.apiKey": self._api_key,
                },
                headers={"Accept": "application/json"},
                timeout=20,
            )

            if resp.status_code != 200:
                return self.failed_result(mpn, f"API返回 {resp.status_code}")

            data = resp.json()
            return self._parse_response(mpn, data)
        except Exception as e:
            logger.error(f"[element14] search failed: {e}")
            return self.failed_result(mpn, str(e))

    async def _search_via_web(self, mpn: str) -> PartResult:
        """Fallback: parse the public element14/e络盟 product page."""
        try:
            client = self._get_client()
            resp = await client.get(
                "https://cn.element14.com/search",
                params={"st": mpn},
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Referer": "https://cn.element14.com/",
                },
                timeout=25,
            )
            if resp.status_code != 200:
                return self.failed_result(mpn, f"网页返回 {resp.status_code}")
            return self._parse_web_page(mpn, resp.text, str(resp.url))
        except Exception as e:
            logger.error(f"[element14] web fallback failed: {e}")
            return self.failed_result(mpn, str(e))

    def _parse_response(self, mpn: str, data: dict) -> PartResult:
        """Parse element14 Product Search API response."""
        search_return = data.get("keywordSearchReturn") or data.get("manufacturerPartNumberSearchReturn") or {}
        products = search_return.get("products") or []

        if not products:
            return self.not_found_result(mpn)

        product = products[0]

        price_breaks = []
        for pb in product.get("prices") or []:
            price_breaks.append({
                "quantity": pb.get("from"),
                "unit_price": pb.get("cost"),
            })

        result_data: dict[str, Any] = {
            "mpn": product.get("translatedManufacturerPartNumber", mpn),
            "sku": product.get("sku"),
            "brand": product.get("brandName") or product.get("vendorName"),
            "description": product.get("displayName"),
            "stock": product.get("stock", {}).get("level") if isinstance(product.get("stock"), dict) else self._to_int(product.get("inv")),
            "moq": product.get("translatedMinimumOrderQuality"),
            "package": product.get("packSize"),
            "product_url": f"https://www.element14.com/product/{product.get('sku', '')}",
            "datasheet_url": None,
            "price_breaks": price_breaks,
        }

        if price_breaks and price_breaks[0].get("unit_price"):
            result_data["price_unit"] = price_breaks[0]["unit_price"]

        # Extract datasheet URL from documents
        for doc in product.get("datasheets") or []:
            if doc.get("url"):
                result_data["datasheet_url"] = doc["url"]
                break

        return self.success_result(mpn, result_data)

    def _parse_web_page(self, mpn: str, html: str, url: str) -> PartResult:
        mpn_norm = self._normalize_text(mpn)
        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = unescape(re.sub(r"\s+", " ", title_match.group(1)).strip()) if title_match else ""
        brand = None
        if title:
            parts = title.split()
            for i, part in enumerate(parts):
                if self._normalize_text(part) == mpn_norm and i + 1 < len(parts):
                    brand = parts[i + 1].strip(",|")
                    break

        sku_match = re.search(r"/dp/([A-Z0-9]+)", url, re.I) or re.search(r"/dp/([A-Z0-9]+)", html, re.I)
        stock_match = re.search(r'"totalCount"\s*:\s*(\d+)', html)
        lead_match = re.search(r'"replenishmentLeadTimeInDays"\s*:\s*(\d+)', html)

        price_breaks = []
        seen = set()
        for qty, price, ccy in re.findall(
            r'"minimumQuantity"\s*:\s*(\d+).*?"bestPriceValue"\s*:\s*"([\d.]+)".*?"bestPriceCurrencyIsoCode"\s*:\s*"([A-Z]+)"',
            html,
            re.S,
        ):
            key = (qty, price, ccy)
            if key in seen:
                continue
            seen.add(key)
            price_breaks.append({"quantity": qty, "unit_price": price})

        if not price_breaks:
            return self.failed_result(mpn, "element14网页返回了型号但未返回可解析价格")

        result_data: dict[str, Any] = {
            "mpn": mpn,
            "sku": sku_match.group(1) if sku_match else None,
            "brand": brand,
            "description": title,
            "stock": stock_match.group(1) if stock_match else None,
            "moq": price_breaks[0]["quantity"],
            "product_url": url,
            "price_breaks": price_breaks,
            "price_unit": price_breaks[0]["unit_price"],
            "lead_time": f"{lead_match.group(1)}天" if lead_match else None,
        }
        return self.success_result(mpn, result_data)
