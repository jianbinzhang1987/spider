"""Mouser adapter — Official Search API V1.

Authentication: API Key (query parameter).
Endpoint: POST https://api.mouser.com/api/v1/search/partnumber
Requires: MOUSER_API_KEY environment variable.

Docs: https://api.mouser.com/api/docs/ui/index
"""

from __future__ import annotations

import os
import logging
from typing import Any

from src.adapters.base import HttpAdapter
from src.adapters.registry import AdapterRegistry
from src.models import PartResult

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.mouser.com/api/v1/search/partnumber"


@AdapterRegistry.register("mouser")
class MouserAdapter(HttpAdapter):
    """Mouser adapter using official Search API."""

    def __init__(self) -> None:
        super().__init__("Mouser", timeout=20.0, min_interval=0.5)
        self._api_key = os.environ.get("MOUSER_API_KEY", "")

    async def search_by_mpn(self, mpn: str) -> PartResult:
        if not self._api_key:
            return self.failed_result(mpn, "缺少MOUSER_API_KEY")

        try:
            client = self._get_client()
            resp = await client.post(
                f"{SEARCH_URL}?apiKey={self._api_key}",
                json={
                    "SearchByPartRequest": {
                        "mouserPartNumber": mpn,
                        "partSearchOptions": "None",
                    }
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=20,
            )

            if resp.status_code != 200:
                return self.failed_result(mpn, f"API返回 {resp.status_code}")

            data = resp.json()
            return self._parse_response(mpn, data)
        except Exception as e:
            logger.error(f"[Mouser] search failed: {e}")
            return self.failed_result(mpn, str(e))

    def _parse_response(self, mpn: str, data: dict) -> PartResult:
        """Parse Mouser Search API response."""
        search_results = data.get("SearchResults", {})
        parts = search_results.get("Parts") or []

        if not parts:
            return self.not_found_result(mpn)

        part = parts[0]

        price_breaks = []
        for pb in part.get("PriceBreaks") or []:
            price_str = pb.get("Price", "").replace("$", "").replace(",", "").strip()
            try:
                price_val = float(price_str)
            except (ValueError, TypeError):
                price_val = None
            price_breaks.append({
                "quantity": pb.get("Quantity"),
                "unit_price": price_val,
            })

        stock_str = part.get("Availability", "0")
        stock = self._to_int(stock_str.split()[0] if stock_str else "0")

        result_data: dict[str, Any] = {
            "mpn": part.get("ManufacturerPartNumber", mpn),
            "sku": part.get("MouserPartNumber"),
            "brand": part.get("Manufacturer"),
            "description": part.get("Description"),
            "stock": stock,
            "moq": self._to_int(part.get("Min")),
            "product_url": part.get("ProductDetailUrl"),
            "datasheet_url": part.get("DataSheetUrl"),
            "price_breaks": price_breaks,
        }

        if price_breaks and price_breaks[0].get("unit_price"):
            result_data["price_unit"] = price_breaks[0]["unit_price"]

        return self.success_result(mpn, result_data)
