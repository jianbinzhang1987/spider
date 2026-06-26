"""立创商城 (LCSC) adapter — Official API with signature authentication.

Authentication: API Key + SHA1 signature.
Endpoint: https://www.lcsc.com/rest/wmsc2agent/search/product
Requires: LCSC_API_KEY + LCSC_API_SECRET environment variables.

Docs: https://www.lcsc.com/docs/openapi/index.html
"""

from __future__ import annotations

import hashlib
import secrets
import time
import logging
import re
from typing import Any
from urllib.parse import quote

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.config import get
from src.models import PartResult

logger = logging.getLogger(__name__)

BASE_URL = "https://www.lcsc.com/rest/wmsc2agent"


@AdapterRegistry.register("lcsc")
class LcscAdapter(BrowserAdapter):
    """立创商城 adapter using official LCSC API."""

    def __init__(self, browser_pool) -> None:
        super().__init__("立创商城", browser_pool)
        self._api_key = get("lcsc.api_key")
        self._api_secret = get("lcsc.api_secret")

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
            return await self._search_via_browser(mpn)

        try:
            auth_params = self._generate_signature()
            from curl_cffi.requests import AsyncSession
            async with AsyncSession(impersonate="chrome124", timeout=20) as client:
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
            return await self._search_via_browser(mpn)

    async def _search_via_browser(self, mpn: str) -> PartResult:
        """Fallback: render LCSC search page and parse visible result text."""
        page = await self._new_page()
        try:
            url = f"https://so.szlcsc.com/global.html?k={quote(mpn)}"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)
            body = await page.locator("body").inner_text(timeout=10000)
            final_url = page.url
            return self._parse_web_text(mpn, body, final_url)
        except Exception as e:
            logger.error(f"[立创商城] browser fallback failed: {e}")
            return self.failed_result(mpn, f"浏览器兜底失败: {e}")
        finally:
            await self._release_page(page)

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

    def _parse_web_text(self, mpn: str, text: str, url: str) -> PartResult:
        if "WAF拦截" in text or "访问受限" in text:
            return self.failed_result(mpn, "立创网页被WAF拦截，需可见浏览器预检或稍后重试")

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        mpn_norm = self._normalize_text(mpn)
        starts = [
            i for i, line in enumerate(lines)
            if self._normalize_text(line) == mpn_norm
            and "品牌" in lines[i:i + 8]
            and any(re.match(r"^\d[\d,]*\+$", item) for item in lines[i:i + 50])
        ]
        try:
            start = starts[0]
        except IndexError:
            if mpn_norm not in self._normalize_text(text):
                return self.not_found_result(mpn)
            start = 0

        next_mpn = len(lines)
        for i in range(start + 1, len(lines)):
            if self._normalize_text(lines[i]) == mpn_norm:
                next_mpn = i
                break
        block = lines[start:next_mpn]
        block_text = "\n".join(block)

        brand = None
        brand_match = re.search(r"品牌\s*\n([^\n]+)", block_text)
        if brand_match:
            brand = brand_match.group(1).strip()

        sku = None
        sku_match = re.search(r"编号\s*\n([A-Z]\d+)", block_text)
        if sku_match:
            sku = sku_match.group(1)

        package = None
        package_match = re.search(r"封装\s*\n([^\n]+)", block_text)
        if package_match:
            package = package_match.group(1).strip()

        stock = None
        stock_match = re.search(r"(?:嘉立创库存|库存)\s*([\d,]+)", block_text)
        if stock_match:
            stock = stock_match.group(1)

        lead_time = "现货" if "现货" in block_text else None
        if not lead_time:
            lead_match = re.search(r"(\d+\s*-\s*\d+个工作日|需订货)", block_text)
            if lead_match:
                lead_time = lead_match.group(1)

        price_breaks = []
        for qty, price in re.findall(r"(\d[\d,]*)\+\s*\n[￥¥]\s*(\d+(?:\.\d+)?)", block_text):
            price_breaks.append({"quantity": qty, "unit_price": price})

        if not price_breaks:
            return self.failed_result(mpn, "立创网页返回了型号但未返回可解析价格")

        result_data: dict[str, Any] = {
            "mpn": mpn,
            "sku": sku,
            "brand": brand,
            "package": package,
            "stock": stock,
            "lead_time": lead_time,
            "product_url": url,
            "price_breaks": price_breaks,
            "price_unit": price_breaks[0]["unit_price"],
        }
        return self.success_result(mpn, result_data)
