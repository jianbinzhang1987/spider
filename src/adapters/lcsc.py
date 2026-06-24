"""立创商城 (LCSC) adapter — Official API with signature authentication.

Authentication: API Key + SHA1 signature.
Endpoint: https://www.lcsc.com/rest/wmsc2agent/search/product
Requires: LCSC_API_KEY + LCSC_API_SECRET environment variables.

Docs: https://www.lcsc.com/docs/openapi/index.html
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
import logging
from typing import Any

from src.adapters.base import HttpAdapter
from src.adapters.registry import AdapterRegistry
from src.models import PartResult

logger = logging.getLogger(__name__)

BASE_URL = "https://www.lcsc.com/rest/wmsc2agent"


@AdapterRegistry.register("lcsc")
class LcscAdapter(HttpAdapter):
    """立创商城 adapter using official LCSC API."""

    def __init__(self) -> None:
        super().__init__("立创商城", timeout=20.0, min_interval=1.0)
        self._api_key = os.environ.get("LCSC_API_KEY", "")
        self._api_secret = os.environ.get("LCSC_API_SECRET", "")

    def _generate_signature(self) -> dict[str, str]:
        """Generate authentication parameters: key, nonce, timestamp, signature."""
        nonce = secrets.token_hex(8)
        timestamp = str(int(time.time()))
        sign_str = f"key={self._api_key}&nonce={nonce}&secret={self._api_secret}&timestamp={timestamp}"
        signature = hashlib.sha1(sign_str.encode()).hexdigest()
        return {
            "key": self._api_key,
            "nonce": nonce,
            "timestamp": timestamp,
            "signature": signature,
        }

    async def search_by_mpn(self, mpn: str) -> PartResult:
        if not self._api_key or not self._api_secret:
            return self.failed_result(mpn, "缺少LCSC_API_KEY/LCSC_API_SECRET")

        try:
            auth_params = self._generate_signature()
            client = self._get_client()
            resp = await client.get(
                f"{BASE_URL}/search/product",
                params={
                    **auth_params,
                    "keyword": mpn,
                    "pageNumber": 1,
                    "pageSize": 10,
                },
                headers={"Accept": "application/json"},
                timeout=20,
            )

            if resp.status_code != 200:
                return self.failed_result(mpn, f"API返回 {resp.status_code}")

            data = resp.json()
            return self._parse_response(mpn, data)
        except Exception as e:
            logger.error(f"[立创商城] search failed: {e}")
            return self.failed_result(mpn, str(e))

    def _parse_response(self, mpn: str, data: dict) -> PartResult:
        """Parse LCSC API response."""
        if data.get("code") not in (200, 0, None):
            error_msg = data.get("msg") or data.get("message") or f"code={data.get('code')}"
            return self.failed_result(mpn, error_msg)

        result = data.get("result") or data.get("data") or {}
        products = result.get("productList") or result.get("dataList") or []

        if not products:
            return self.not_found_result(mpn)

        product = products[0]

        price_breaks = []
        for pb in product.get("productPriceList") or product.get("priceList") or []:
            price_breaks.append({
                "quantity": pb.get("startNumber") or pb.get("ladder"),
                "unit_price": pb.get("productPrice") or pb.get("usdPrice"),
            })

        result_data: dict[str, Any] = {
            "mpn": product.get("productModel") or product.get("manufacturerPartNumber", mpn),
            "sku": product.get("productCode") or product.get("lcscPartNumber"),
            "brand": product.get("brandNameEn") or product.get("brandNameCn"),
            "description": product.get("productIntroEn") or product.get("catalogName"),
            "stock": product.get("stockNumber") or product.get("stockQty"),
            "moq": product.get("minBuyNumber"),
            "package": product.get("encapStandard") or product.get("packageName"),
            "product_url": f"https://www.lcsc.com/product-detail/{product.get('productCode', '')}.html",
            "datasheet_url": product.get("pdfUrl"),
            "price_breaks": price_breaks,
        }

        if price_breaks and price_breaks[0].get("unit_price"):
            result_data["price_unit"] = price_breaks[0]["unit_price"]

        return self.success_result(mpn, result_data)
