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
        best_match = None
        best_price = None

        for resp in responses:
            data_groups = resp.get("data", [])
            if not isinstance(data_groups, list):
                # Some responses have data as a dict
                if isinstance(data_groups, dict):
                    data_groups = [data_groups]
                else:
                    continue

            for group in data_groups:
                if not isinstance(group, dict):
                    continue
                items = group.get("data", [])
                if not isinstance(items, list):
                    # Try items directly from group
                    items = group.get("list") or group.get("items") or []
                    if not isinstance(items, list):
                        continue

                for item in items:
                    if not isinstance(item, dict):
                        continue
                    goods_name = item.get("goods_name") or item.get("goods_sn") or ""
                    if mpn_norm not in self._normalize_text(goods_name):
                        continue

                    # Price: try multiple field names (expanded)
                    price = None
                    for field in ("price", "single_price", "goods_price", "cn_price",
                                  "unit_price", "sell_price", "min_price",
                                  "price_cn", "cny_price", "rmb_price"):
                        val = item.get(field)
                        if val:
                            try:
                                p = float(val)
                                if 0.0001 < p < 100000:
                                    price = p
                                    break
                            except (ValueError, TypeError):
                                pass

                    # Try ladder/price list
                    price_breaks = []
                    ladder = (item.get("ladder_price") or item.get("prices")
                              or item.get("price_list") or item.get("price_breaks") or [])
                    if isinstance(ladder, list):
                        for lb in ladder:
                            if isinstance(lb, dict):
                                qty = lb.get("purchases") or lb.get("qty") or lb.get("num") or lb.get("quantity")
                                p = lb.get("price_cn") or lb.get("price") or lb.get("unit_price") or lb.get("cny_price")
                                if p:
                                    try:
                                        price_value = float(p)
                                    except (ValueError, TypeError):
                                        continue
                                    if not (0.0001 < price_value < 100000):
                                        continue
                                    qty_value = self._to_int(qty) or 1
                                    price_breaks.append({"quantity": qty_value, "unit_price": price_value})
                        if not price and price_breaks:
                            price = min(pb["unit_price"] for pb in price_breaks)

                    # Also try nested "offer" or "supplier" structures
                    if not price:
                        for nested_key in ("offer", "supplier_info", "goods_info"):
                            nested = item.get(nested_key)
                            if isinstance(nested, dict):
                                for field in ("price", "cn_price", "unit_price", "sell_price"):
                                    val = nested.get(field)
                                    if val:
                                        try:
                                            p = float(val)
                                            if 0.0001 < p < 100000:
                                                price = p
                                                break
                                        except (ValueError, TypeError):
                                            pass
                                if price:
                                    break

                    # Track best match (prefer one with price)
                    if best_match is None or (price and best_price is None):
                        best_match = item
                        best_price = price
                        best_match["_price_breaks"] = price_breaks

        if best_match:
            price_breaks = best_match.get("_price_breaks", [])
            if best_price is None and price_breaks:
                best_price = min(pb["unit_price"] for pb in price_breaks)
            return self.success_result(mpn, {
                "mpn": best_match.get("goods_name") or best_match.get("goods_sn") or mpn,
                "brand": best_match.get("brand_name") or best_match.get("brand"),
                "stock": best_match.get("stock") or best_match.get("goods_number") or best_match.get("number"),
                "price_unit": best_price,
                "price_breaks": price_breaks,
                "package": best_match.get("encap") or best_match.get("package"),
                "lead_time": self._format_lead_time(best_match),
                "product_url": f"https://www.ichunt.com/s/?k={mpn}",
            })

        return self.not_found_result(mpn)

    def _format_lead_time(self, item: dict[str, Any]) -> str | None:
        parts = []
        cn = item.get("cn_delivery_time_origin") or item.get("cn_delivery_time")
        if cn:
            parts.append(f"大陆{cn if '工作日' in str(cn) else str(cn) + '工作日'}")
        hk = item.get("hk_delivery_time_origin") or item.get("hk_delivery_time")
        if hk:
            parts.append(f"香港{hk if '工作日' in str(hk) else str(hk) + '工作日'}")
        return "；".join(parts) if parts else None

    def _parse_dom(self, mpn: str, html: str) -> PartResult:
        """Fallback: parse rendered DOM for product data."""
        mpn_norm = self._normalize_text(mpn)

        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        price_values: list[float] = []

        # Pattern 1: Currency symbol followed by number
        prices = re.findall(r'[￥¥]\s*(\d+\.?\d*)', html)
        price_values.extend(float(p) for p in prices if 0.001 < float(p) < 100000)

        # Pattern 2: Price in JSON/data attributes
        data_prices = re.findall(r'"(?:price|goods_price|single_price|cn_price|unit_price)"[:\s]*["\']?(\d+\.?\d*)', html)
        price_values.extend(float(p) for p in data_prices if 0.001 < float(p) < 100000)

        # Pattern 3: Ladder price text (e.g., "1+ ¥0.05")
        ladder_prices = re.findall(r'\d+\+\s*[￥¥]?\s*(\d+\.?\d+)', html)
        price_values.extend(float(p) for p in ladder_prices if 0.001 < float(p) < 100000)

        result_data: dict[str, Any] = {
            "mpn": mpn,
            "product_url": f"https://www.ichunt.com/s/?k={mpn}",
        }

        if price_values:
            result_data["price_unit"] = min(price_values)

        return self.success_result(mpn, result_data)
