"""Digi-Key adapter — Official Product Information API V4.

Authentication: OAuth 2.0 (2-legged, client_credentials).
Endpoint: https://api.digikey.com/products/v4/search/{mpn}/productdetails
Requires: DIGIKEY_CLIENT_ID + DIGIKEY_CLIENT_SECRET environment variables.

Docs: https://developer.digikey.com/products/product-information-v4
"""

from __future__ import annotations

import time
import logging
import re
from typing import Any
from urllib.parse import quote

from curl_cffi.requests import AsyncSession

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.config import get
from src.core.browser_pool import BrowserPool
from src.models import PartResult

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
SEARCH_URL = "https://api.digikey.com/products/v4/search/keyword"


@AdapterRegistry.register("digikey")
class DigikeyAdapter(BrowserAdapter):
    """Digi-Key adapter using API when configured, otherwise browser fallback."""

    def __init__(self, browser_pool: BrowserPool) -> None:
        super().__init__("Digi-Key", browser_pool)
        self._client_id = get("digikey.client_id")
        self._client_secret = get("digikey.client_secret")
        self._access_token: str | None = None
        self._token_expires_at: float = 0

    async def search_by_mpn(self, mpn: str) -> PartResult:
        if not self._client_id or not self._client_secret:
            return await self._search_via_browser(mpn)

        token = await self._get_token()
        if not token:
            return self.failed_result(mpn, "OAuth token获取失败")

        try:
            async with AsyncSession(impersonate="chrome124", timeout=20) as client:
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
            async with AsyncSession(impersonate="chrome124", timeout=15) as client:
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

    async def _search_via_browser(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            url = f"https://www.digikey.cn/zh/products/result?keywords={quote(mpn)}"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(9000)
            try:
                text = await page.locator("body").inner_text(timeout=5000)
            except Exception:
                text = ""
            content = await page.content()
            result = self._parse_web(mpn, f"{content}\n{text}", url)
            if result.status.value == "success" or self._is_access_limited(f"{content}\n{text}"):
                return result

            detail_url = await self._find_product_detail_url(page, mpn)
            if detail_url:
                await page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(9000)
                try:
                    detail_text = await page.locator("body").inner_text(timeout=5000)
                except Exception:
                    detail_text = ""
                detail_content = await page.content()
                return self._parse_web(mpn, f"{detail_content}\n{detail_text}", detail_url)

            return result
        except Exception as e:
            logger.error(f"[Digi-Key] browser search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    def _parse_web(self, mpn: str, html: str, url: str) -> PartResult:
        mpn_norm = self._normalize_text(mpn)
        if self._is_access_limited(html):
            return self.failed_result(
                mpn,
                "Digi-Key访问受限或验证码未通过；请先在Web页面点击“验证Digi-Key”保存session，或配置DIGIKEY_CLIENT_ID/DIGIKEY_CLIENT_SECRET",
            )

        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        prices = re.findall(r'(?:￥|¥|CN¥|\$)\s*(\d+(?:\.\d+)?)', html)
        price_values = [float(p) for p in prices if 0.0001 < float(p) < 100000]
        stock_match = re.search(r'(?:现货|库存|In Stock)[^\d]{0,20}([\d,]+)', html, re.I)
        brand_match = re.search(r'(?:制造商|Manufacturer|品牌)[：:\s]*([A-Za-z0-9 .,&\-]+)', html, re.I)

        data: dict[str, Any] = {
            "mpn": mpn,
            "brand": brand_match.group(1).strip() if brand_match else None,
            "stock": stock_match.group(1) if stock_match else None,
            "product_url": url,
            "price_currency": "CNY" if "￥" in html or "CN¥" in html else "USD",
        }
        if price_values:
            data["price_unit"] = min(price_values)
        else:
            return self.failed_result(
                mpn,
                "Digi-Key返回了匹配型号但未返回可解析价格；可能需要进入详情页、登录/验证session或使用官方API",
            )
        return self.success_result(mpn, data)

    def _is_access_limited(self, html: str) -> bool:
        signals = [
            "访问暂时受限",
            "自动化工具",
            "captcha",
            "Access Denied",
            "Reference ID",
            "blocked",
        ]
        return any(signal.lower() in html.lower() for signal in signals)

    async def _find_product_detail_url(self, page, mpn: str) -> str | None:
        try:
            href = await page.evaluate(
                """(mpn) => {
                    const norm = (s) => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
                    const target = norm(mpn);
                    for (const a of document.querySelectorAll('a[href]')) {
                        const href = a.href || '';
                        const text = a.textContent || '';
                        if (href.includes('/products/detail/') && (norm(href).includes(target) || norm(text).includes(target))) {
                            return href;
                        }
                    }
                    for (const a of document.querySelectorAll('a[href*="/products/detail/"]')) {
                        return a.href;
                    }
                    return null;
                }""",
                mpn,
            )
            return href
        except Exception:
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
