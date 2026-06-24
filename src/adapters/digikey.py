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

        # Find best matching product (V4 uses ManufacturerProductNumber)
        mpn_norm = self._normalize_text(mpn)
        product = None
        for p in products:
            p_mpn = p.get("ManufacturerProductNumber") or p.get("ManufacturerPartNumber") or ""
            if mpn_norm == self._normalize_text(p_mpn):
                product = p
                break
        if not product:
            product = products[0]

        # Extract price breaks — prefer full ladder from ProductVariations
        price_breaks = []

        # First, try ProductVariations (contains packaging-specific pricing)
        variations = product.get("ProductVariations") or []
        for var in variations:
            sp = var.get("StandardPricing") or []
            if sp and len(sp) > len(price_breaks):
                # Use the variation with the most price breaks (fullest ladder)
                candidate = []
                for pb in sp:
                    qty = pb.get("BreakQuantity") or pb.get("Quantity")
                    price = pb.get("UnitPrice") or pb.get("Price")
                    if qty and price:
                        candidate.append({"quantity": qty, "unit_price": price})
                if len(candidate) > len(price_breaks):
                    price_breaks = candidate

        # Fallback to top-level StandardPricing
        if not price_breaks:
            for pb in product.get("StandardPricing") or []:
                qty = pb.get("BreakQuantity") or pb.get("Quantity")
                price = pb.get("UnitPrice") or pb.get("Price")
                if qty and price:
                    price_breaks.append({"quantity": qty, "unit_price": price})

        # Also try direct unit price field
        unit_price = None
        if price_breaks:
            unit_price = price_breaks[0].get("unit_price")
        if not unit_price:
            # Try UnitPrice as a direct numeric value
            for field in ("UnitPrice", "unitPrice", "SearchLocaleUnitPrice"):
                val = product.get(field)
                if isinstance(val, (int, float)) and val > 0:
                    unit_price = val
                    break

        # Get SKU from first variation if not at top level
        sku = product.get("DigiKeyPartNumber")
        if not sku and product.get("ProductVariations"):
            sku = product["ProductVariations"][0].get("DigiKeyProductNumber")

        # Get MOQ from first variation
        moq = product.get("MinimumOrderQuantity")
        if not moq and product.get("ProductVariations"):
            moq = product["ProductVariations"][0].get("MinimumOrderQuantity")

        result_data: dict[str, Any] = {
            "mpn": product.get("ManufacturerProductNumber") or product.get("ManufacturerPartNumber") or mpn,
            "sku": sku,
            "brand": product.get("Manufacturer", {}).get("Name") if isinstance(product.get("Manufacturer"), dict) else product.get("Manufacturer"),
            "description": product.get("ProductDescription") or (product.get("Description", {}).get("DetailedDescription") if isinstance(product.get("Description"), dict) else product.get("Description")),
            "stock": product.get("QuantityAvailable"),
            "moq": moq,
            "package": product.get("Packaging", {}).get("Value") if isinstance(product.get("Packaging"), dict) else None,
            "product_url": product.get("ProductUrl"),
            "datasheet_url": product.get("DatasheetUrl") or (product.get("PrimaryDatasheet") if isinstance(product.get("PrimaryDatasheet"), str) else None),
            "price_breaks": price_breaks,
            "price_unit": unit_price,
            "price_currency": "USD",
        }

        return self.success_result(mpn, result_data)
