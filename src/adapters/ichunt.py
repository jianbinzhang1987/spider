"""猎芯网 (ichunt.com) adapter — Playwright with SPA interaction."""

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
    """
    猎芯网 adapter.

    Strategy: Playwright renders SPA → interact with search → intercept API responses.
    Known: api.ichunt.com / apibom.ichunt.com (exact endpoints TBD).
    Note: May require China IP for full functionality.
    """

    def __init__(self, browser_pool: BrowserPool) -> None:
        super().__init__("猎芯网", browser_pool)

    async def search_by_mpn(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            api_responses: list[dict] = []

            async def capture_api(response):
                url = response.url
                if "api.ichunt.com" in url or "apibom.ichunt.com" in url:
                    try:
                        text = await response.text()
                        if text and (text.startswith("{") or text.startswith("[")):
                            api_responses.append({
                                "url": url,
                                "data": json.loads(text),
                            })
                    except Exception:
                        pass

            page.on("response", capture_api)

            # Load homepage
            await page.goto("https://www.ichunt.com/", timeout=20000)
            await page.wait_for_timeout(3000)

            # Try to find search input and submit
            search_input = await page.query_selector(
                'input[type="text"], input.search-input, [placeholder*="搜索"]'
            )
            if search_input:
                await search_input.fill(mpn)
                await search_input.press("Enter")
                await page.wait_for_timeout(8000)
            else:
                # Try direct URL navigation
                await page.goto(f"https://www.ichunt.com/search?keyword={mpn}", timeout=20000)
                await page.wait_for_timeout(8000)

            # Check intercepted API data
            if api_responses:
                result = self._parse_api_responses(mpn, api_responses)
                if result.status.value == "success":
                    return result

            # Fallback: parse DOM
            content = await page.content()
            return self._parse_dom(mpn, content)
        except Exception as e:
            logger.error(f"[猎芯网] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    def _parse_api_responses(self, mpn: str, responses: list[dict]) -> PartResult:
        """Parse intercepted API responses for product data."""
        for resp in responses:
            data = resp.get("data", {})
            if not isinstance(data, dict):
                continue

            # Look for product items in common structures
            items = data.get("data") or data.get("list") or data.get("items")
            if isinstance(items, list) and items:
                item = items[0]
                if isinstance(item, dict):
                    return self.success_result(mpn, {
                        "mpn": item.get("goods_name") or item.get("partno") or mpn,
                        "brand": item.get("brand_name") or item.get("manufacturer"),
                        "stock": item.get("stock") or item.get("inventory"),
                        "price_unit": item.get("price") or item.get("unit_price"),
                        "product_url": f"https://www.ichunt.com/search?keyword={mpn}",
                    })

        return self.not_found_result(mpn)

    def _parse_dom(self, mpn: str, html: str) -> PartResult:
        """Fallback: parse rendered DOM."""
        mpn_norm = self._normalize_text(mpn)

        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        prices = re.findall(r'[￥¥]\s*(\d+\.?\d*)', html)
        price_values = [float(p) for p in prices if 0.0001 < float(p) < 100000]

        result_data: dict[str, Any] = {
            "mpn": mpn,
            "product_url": f"https://www.ichunt.com/search?keyword={mpn}",
        }

        if price_values:
            result_data["price_unit"] = min(price_values)

        return self.success_result(mpn, result_data)
