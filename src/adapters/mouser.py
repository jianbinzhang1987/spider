"""Mouser adapter — Official Search API V1.

Authentication: API Key (query parameter).
Endpoint: POST https://api.mouser.com/api/v1/search/partnumber
Requires: MOUSER_API_KEY environment variable.

Docs: https://api.mouser.com/api/docs/ui/index
"""

from __future__ import annotations

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

SEARCH_URL = "https://api.mouser.com/api/v1/search/partnumber"


@AdapterRegistry.register("mouser")
class MouserAdapter(BrowserAdapter):
    """Mouser adapter using API when configured, otherwise browser fallback."""

    def __init__(self, browser_pool: BrowserPool) -> None:
        super().__init__("Mouser", browser_pool)
        self._api_key = get("mouser.api_key")

    async def search_by_mpn(self, mpn: str) -> PartResult:
        if not self._api_key:
            return await self._search_via_browser(mpn)

        try:
            async with AsyncSession(impersonate="chrome124", timeout=20) as client:
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

    async def _search_via_browser(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            url = f"https://www.mouser.cn/c/?q={quote(mpn)}"
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
            logger.error(f"[Mouser] browser search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    def _parse_web(self, mpn: str, html: str, url: str) -> PartResult:
        mpn_norm = self._normalize_text(mpn)
        if self._is_access_limited(html):
            return self.failed_result(
                mpn,
                "Mouser访问暂时受限，页面判定当前浏览器为自动化访问；请先在Web页面点击“验证Mouser”保存session，或配置MOUSER_API_KEY",
            )

        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        prices = re.findall(r'(?:￥|¥|CN¥|\$)\s*(\d+(?:\.\d+)?)', html)
        price_values = [float(p) for p in prices if 0.0001 < float(p) < 100000]
        stock_match = re.search(r'(?:库存|有货|In Stock|Availability)[^\d]{0,30}([\d,]+)', html, re.I)
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
                "Mouser返回了匹配型号但未返回可解析价格；可能是商品详情页价格受限、需要验证session或使用官方API",
            )
        return self.success_result(mpn, data)

    def _is_access_limited(self, html: str) -> bool:
        signals = [
            "访问暂时受限",
            "自动化工具浏览本网站",
            "Reference ID",
            "您的浏览器不支持cookie",
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
                        if (href.includes('/ProductDetail/') && (norm(href).includes(target) || norm(text).includes(target))) {
                            return href;
                        }
                    }
                    for (const a of document.querySelectorAll('a[href*="/ProductDetail/"]')) {
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
        """Parse Mouser Search API response."""
        search_results = data.get("SearchResults", {})
        parts = search_results.get("Parts") or []

        if not parts:
            return self.not_found_result(mpn)

        # Find best matching part
        mpn_norm = self._normalize_text(mpn)
        part = None
        for p in parts:
            p_mpn = p.get("ManufacturerPartNumber", "")
            if mpn_norm == self._normalize_text(p_mpn):
                part = p
                break
        if not part:
            part = parts[0]

        price_breaks = []
        currency = "CNY"  # Default; detect from API response
        for pb in part.get("PriceBreaks") or []:
            price_val = self._parse_price_str(pb.get("Price", ""))
            qty = pb.get("Quantity")
            if price_val and qty:
                price_breaks.append({
                    "quantity": qty,
                    "unit_price": price_val,
                })
            # Detect currency from first PriceBreak
            pb_currency = pb.get("Currency", "")
            if pb_currency in ("USD", "US"):
                currency = "USD"
            elif pb_currency in ("RMB", "CNY", ""):
                currency = "CNY"

        stock_str = part.get("Availability", "0")
        # Availability can be "0" or "300,325 In Stock" etc.
        stock_num = "0"
        if stock_str:
            import re
            nums = re.findall(r'[\d,]+', stock_str)
            if nums:
                stock_num = nums[0]
        stock = self._to_int(stock_num)

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
            "price_currency": currency,
        }

        if price_breaks and price_breaks[0].get("unit_price"):
            result_data["price_unit"] = price_breaks[0]["unit_price"]

        return self.success_result(mpn, result_data)

    @staticmethod
    def _parse_price_str(price_str: str) -> float | None:
        """Parse price string in various currency formats (¥1.23, $0.52, CN¥1.23, etc.)."""
        if not price_str:
            return None
        import re
        # Remove all currency symbols and whitespace, keep digits and decimal point
        cleaned = re.sub(r'[^\d.]', '', price_str)
        if not cleaned:
            return None
        try:
            val = float(cleaned)
            return val if val > 0 else None
        except (ValueError, TypeError):
            return None
