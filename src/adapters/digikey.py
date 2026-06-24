"""Digi-Key adapter — Official Product Information API V4.

Authentication: OAuth 2.0 (2-legged, client_credentials).
Endpoint: https://api.digikey.com/products/v4/search/{mpn}/productdetails
Requires: DIGIKEY_CLIENT_ID + DIGIKEY_CLIENT_SECRET environment variables.

Docs: https://developer.digikey.com/products/product-information-v4
"""

from __future__ import annotations

import time
import logging
from typing import Any

from src.adapters.base import HttpAdapter
from src.adapters.registry import AdapterRegistry
from src.config import get
from src.models import PartResult

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
SEARCH_URL = "https://api.digikey.com/products/v4/search/keyword"


@AdapterRegistry.register("digikey")
class DigikeyAdapter(HttpAdapter):
    """Digi-Key adapter using official Product Information API V4."""

    def __init__(self) -> None:
        super().__init__("Digi-Key", timeout=20.0, min_interval=0.5)
        self._client_id = get("digikey.client_id")
        self._client_secret = get("digikey.client_secret")
        self._access_token: str | None = None
        self._token_expires_at: float = 0

    async def search_by_mpn(self, mpn: str) -> PartResult:
        if not self._client_id or not self._client_secret:
            return self.failed_result(mpn, "缺少DIGIKEY_CLIENT_ID/DIGIKEY_CLIENT_SECRET")

        token = await self._get_token()
        if not token:
            return self.failed_result(mpn, "OAuth token获取失败")

        try:
            client = self._get_client()
            resp = await client.post(
                SEARCH_URL,
                json={
                    "Keywords": mpn,
                    "RecordCount": 10,
                    "RecordStartPosition": 0,
                    "ExcludeMarketPlaceProducts": False,
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-DIGIKEY-Client-Id": self._client_id,
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
            logger.error(f"[Digi-Key] search failed: {e}")
            return self.failed_result(mpn, str(e))

    async def _get_token(self) -> str | None:
        """Get or refresh OAuth2 access token."""
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        try:
            client = self._get_client()
            resp = await client.post(
                TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )

            if resp.status_code != 200:
                logger.error(f"[Digi-Key] Token error: {resp.status_code} {resp.text[:200]}")
                return None

            token_data = resp.json()
            self._access_token = token_data["access_token"]
            self._token_expires_at = time.time() + token_data.get("expires_in", 3600)
            return self._access_token
        except Exception as e:
            logger.error(f"[Digi-Key] Token request failed: {e}")
            return None

    def _parse_response(self, mpn: str, data: dict) -> PartResult:
        """Parse Digi-Key API V4 keyword search response."""
        products = data.get("Products") or data.get("ExactManufacturerProducts") or []
        if not products:
            return self.not_found_result(mpn)

        product = products[0]

        price_breaks = []
        for pb in product.get("StandardPricing") or []:
            price_breaks.append({
                "quantity": pb.get("BreakQuantity"),
                "unit_price": pb.get("UnitPrice"),
            })

        result_data: dict[str, Any] = {
            "mpn": product.get("ManufacturerPartNumber", mpn),
            "sku": product.get("DigiKeyPartNumber"),
            "brand": product.get("Manufacturer", {}).get("Name"),
            "description": product.get("ProductDescription"),
            "stock": product.get("QuantityAvailable"),
            "moq": product.get("MinimumOrderQuantity"),
            "package": product.get("Packaging", {}).get("Value") if isinstance(product.get("Packaging"), dict) else None,
            "product_url": product.get("ProductUrl"),
            "datasheet_url": product.get("DatasheetUrl"),
            "price_breaks": price_breaks,
        }

        if price_breaks:
            result_data["price_unit"] = price_breaks[0].get("unit_price")

        return self.success_result(mpn, result_data)
