"""element14/Farnell/Newark adapter — Official Product Search API (REST).

Authentication: API Key (query parameter).
Endpoint: https://api.element14.com/catalog/products
Requires: ELEMENT14_API_KEY environment variable.
Optional: ELEMENT14_STORE (default: "cn.element14.com")

Docs: https://partner.element14.com/docs
"""

from __future__ import annotations

import logging
from typing import Any

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
            return self.failed_result(mpn, "缺少ELEMENT14_API_KEY")

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
