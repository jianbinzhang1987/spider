"""猎芯网 (ichunt.com) adapter — Playwright with SPA + API interception.

Verified approach:
  - Site detects overseas IP and redirects to /v3/info (limited static page)
  - X-Forwarded-For: 114.114.114.114 bypasses geo-detection
  - Correct search URL: https://www.ichunt.com/s/?k={mpn}
  - Product data loaded via icso.ichunt.com/search/getData/indexRealTime (JSON)
  - API requires browser session cookies (cannot call directly)
  - Intercept response to extract structured product data
"""

from __future__ import annotations

import json
import re
import logging
from typing import Any

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool
from src.models import PartResult

logger = logging.getLogger(__name__)


@AdapterRegistry.register("ichunt")
class IchuntAdapter(BrowserAdapter):
    """猎芯网 adapter — Playwright SPA + icso API interception."""

    SEARCH_URL = "https://www.ichunt.com/s/"

    def __init__(self, browser_pool: BrowserPool) -> None:
        super().__init__("猎芯网", browser_pool)

    async def search_by_mpn(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            # Bypass geo-detection (site redirects overseas users to /v3/info)
            await page.set_extra_http_headers({
                "X-Forwarded-For": "114.114.114.114",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })

            api_responses: list[dict] = []

            async def capture_api(response):
                """Intercept icso.ichunt.com API responses containing product data."""
                url = response.url
                if "icso.ichunt.com" not in url:
                    return
                try:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct:
                        text = await response.text()
                        if text and text.startswith("{"):
                            data = json.loads(text)
                            if data.get("error_code") == 0 and data.get("data"):
                                api_responses.append(data)
                except Exception:
                    pass

            page.on("response", capture_api)

            url = f"{self.SEARCH_URL}?k={mpn}"
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            except Exception:
                pass
            # Wait for SPA to load and API calls to complete
            await page.wait_for_timeout(12000)

            # Check intercepted API responses
            if api_responses:
                result = self._parse_api_responses(mpn, api_responses)
                if result.status.value == "success":
                    return result

            # Check if page was redirected to /v3/info (geo-block still active)
            if "/v3/info" in page.url:
                return self.failed_result(mpn, "地域限制 - 需要国内IP")

            # Fallback: parse rendered DOM
            content = await page.content()
            return self._parse_dom(mpn, content)
        except Exception as e:
            logger.error(f"[猎芯网] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    def _parse_api_responses(self, mpn: str, responses: list[dict]) -> PartResult:
        """Parse icso.ichunt.com API responses.

        Structure: {error_code: 0, data: [{coupon_id, data: [product, ...]}]}
        """
        mpn_norm = self._normalize_text(mpn)

        for resp in responses:
            data_groups = resp.get("data", [])
            if not isinstance(data_groups, list):
                continue

            for group in data_groups:
                if not isinstance(group, dict):
                    continue
                items = group.get("data", [])
                if not isinstance(items, list):
                    continue

                for item in items:
                    if not isinstance(item, dict):
                        continue
                    goods_name = item.get("goods_name", "")
                    if mpn_norm in self._normalize_text(goods_name):
                        # Price: try multiple field names
                        price = (
                            item.get("price") or item.get("single_price")
                            or item.get("goods_price") or item.get("cn_price")
                            or item.get("unit_price") or item.get("sell_price")
                            or item.get("min_price")
                        )
                        # Try ladder/price list
                        price_breaks = []
                        ladder = item.get("ladder_price") or item.get("prices") or item.get("price_list") or []
                        if isinstance(ladder, list):
                            for lb in ladder:
                                if isinstance(lb, dict):
                                    qty = lb.get("purchases") or lb.get("qty") or lb.get("num")
                                    p = lb.get("price_cn") or lb.get("price") or lb.get("unit_price")
                                    if qty and p:
                                        price_breaks.append({"quantity": qty, "unit_price": p})
                            if not price and price_breaks:
                                price = price_breaks[0]["unit_price"]
                        return self.success_result(mpn, {
                            "mpn": goods_name or mpn,
                            "brand": item.get("brand_name") or item.get("brand"),
                            "stock": item.get("stock") or item.get("goods_number") or item.get("number"),
                            "price_unit": price,
                            "price_breaks": price_breaks,
                            "package": item.get("encap") or item.get("package"),
                            "product_url": f"https://www.ichunt.com/s/?k={mpn}",
                        })

        return self.not_found_result(mpn)

    def _parse_dom(self, mpn: str, html: str) -> PartResult:
        """Fallback: parse rendered DOM for product data."""
        mpn_norm = self._normalize_text(mpn)

        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        prices = re.findall(r'[￥¥]\s*(\d+\.?\d*)', html)
        price_values = [float(p) for p in prices if 0.001 < float(p) < 100000]

        result_data: dict[str, Any] = {
            "mpn": mpn,
            "product_url": f"https://www.ichunt.com/s/?k={mpn}",
        }

        if price_values:
            result_data["price_unit"] = min(price_values)

        return self.success_result(mpn, result_data)
