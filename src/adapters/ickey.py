"""云汉芯城 (ickey.cn) adapter — Official free data API.

Authentication: API Key + Secret (apply at https://www.ickey.cn/api).
Endpoint: https://www.ickey.cn/rest/search/product (assumed based on docs)
Requires: ICKEY_API_KEY + ICKEY_API_SECRET environment variables.

Alternative: Use the HTTP search page directly (curl_cffi fallback).
"""

from __future__ import annotations

import logging
from typing import Any

from src.adapters.base import HttpAdapter
from src.adapters.registry import AdapterRegistry
from src.config import get
from src.models import PartResult

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.ickey.cn/search/product"


@AdapterRegistry.register("ickey")
class IckeyAdapter(HttpAdapter):
    """云汉芯城 adapter — API with HTTP fallback.

    Strategy:
    1. If ICKEY_API_KEY is set, use official data API
    2. Otherwise, attempt HTTP search page (may hit captcha from overseas)
    """

    def __init__(self) -> None:
        super().__init__("云汉芯城", timeout=20.0, min_interval=1.0)
        self._api_key = get("ickey.api_key")
        self._api_secret = get("ickey.api_secret")

    async def search_by_mpn(self, mpn: str) -> PartResult:
        if self._api_key and self._api_secret:
            return await self._search_via_api(mpn)
        return await self._search_via_http(mpn)

    async def _search_via_api(self, mpn: str) -> PartResult:
        """Search using official ickey.cn data API."""
        try:
            client = self._get_client()
            resp = await client.get(
                "https://www.ickey.cn/rest/search/product",
                params={
                    "keyword": mpn,
                    "apiKey": self._api_key,
                    "secret": self._api_secret,
                    "pageNumber": 1,
                    "pageSize": 10,
                },
                headers={"Accept": "application/json"},
                timeout=20,
            )

            if resp.status_code != 200:
                logger.warning(f"[云汉芯城] API returned {resp.status_code}, falling back to HTTP")
                return await self._search_via_http(mpn)

            data = resp.json()
            return self._parse_api_response(mpn, data)
        except Exception as e:
            logger.warning(f"[云汉芯城] API failed: {e}, falling back to HTTP")
            return await self._search_via_http(mpn)

    async def _search_via_http(self, mpn: str) -> PartResult:
        """Fallback: search via HTTP (curl_cffi with TLS fingerprint)."""
        try:
            client = self._get_client()
            resp = await client.get(
                f"https://www.ickey.cn/search/product?keyword={mpn}",
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Referer": "https://www.ickey.cn/",
                },
                timeout=15,
            )

            if resp.status_code != 200:
                return self.failed_result(mpn, f"HTTP {resp.status_code}")

            html = resp.text
            return self._parse_html(mpn, html)
        except Exception as e:
            logger.error(f"[云汉芯城] HTTP search failed: {e}")
            return self.failed_result(mpn, str(e))

    def _parse_api_response(self, mpn: str, data: dict) -> PartResult:
        """Parse official API JSON response."""
        if data.get("code") not in (200, 0, "0", None):
            return self.failed_result(mpn, data.get("msg", "API error"))

        products = data.get("data") or data.get("result", {}).get("list") or []
        if not products:
            return self.not_found_result(mpn)

        product = products[0] if isinstance(products, list) else {}

        price_breaks = []
        for pb in product.get("priceList") or product.get("ladderPrices") or []:
            price_breaks.append({
                "quantity": pb.get("startQty") or pb.get("minQty"),
                "unit_price": pb.get("price") or pb.get("unitPrice"),
            })

        result_data: dict[str, Any] = {
            "mpn": product.get("partNumber") or product.get("goodsName", mpn),
            "sku": product.get("skuCode"),
            "brand": product.get("brandName") or product.get("manufacturer"),
            "description": product.get("description"),
            "stock": product.get("stockQty") or product.get("inventory"),
            "moq": product.get("moq"),
            "package": product.get("packageName") or product.get("encap"),
            "product_url": f"https://www.ickey.cn/product/detail/{product.get('skuCode', '')}.html",
            "datasheet_url": product.get("pdfUrl"),
            "price_breaks": price_breaks,
        }

        if price_breaks and price_breaks[0].get("unit_price"):
            result_data["price_unit"] = price_breaks[0]["unit_price"]

        return self.success_result(mpn, result_data)

    def _parse_html(self, mpn: str, html: str) -> PartResult:
        """Fallback: parse HTML search results."""
        import re
        mpn_norm = self._normalize_text(mpn)

        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        prices = re.findall(r'[￥¥]\s*(\d+\.?\d*)', html)
        price_values = [float(p) for p in prices if 0.001 < float(p) < 100000]

        brand = None
        brand_match = re.search(r'(?:品牌|brand|厂商)[^>]*>([^<]{2,30})<', html, re.I)
        if brand_match:
            brand = brand_match.group(1).strip()

        stock = None
        stock_match = re.search(r'(?:库存|stock|现货)[^>]*>(\d[\d,]*)', html, re.I)
        if stock_match:
            stock = self._to_int(stock_match.group(1))

        result_data: dict[str, Any] = {
            "mpn": mpn,
            "brand": brand,
            "stock": stock,
            "product_url": f"https://www.ickey.cn/search/product?keyword={mpn}",
        }

        if price_values:
            result_data["price_unit"] = min(price_values)

        return self.success_result(mpn, result_data)
