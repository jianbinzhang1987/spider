"""ICGOO (icgoo.net) adapter — Playwright with API response interception."""

from __future__ import annotations

import json
import logging
from typing import Any

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool
from src.models import PartResult, PriceBreak

logger = logging.getLogger(__name__)


@AdapterRegistry.register("icgoo")
class IcgooAdapter(BrowserAdapter):
    """
    ICGOO adapter.

    Strategy: Playwright renders Vue SPA → intercept API responses from v8back.icgoo.net.
    Known APIs (browser-session only):
      - /api/search/suggestions/?q={keyword} → matching part numbers
      - /api/search/supplier/{partno}/1/ → supplier/price data
      - /api/search/batch_price/ → batch pricing
    Note: These APIs return HTML (SPA shell) when called directly via curl_cffi,
          but return JSON when called within a browser session.
    """

    def __init__(self, browser_pool: BrowserPool) -> None:
        super().__init__("ICGOO", browser_pool)

    async def search_by_mpn(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            api_data: dict[str, Any] = {}

            async def capture_response(response):
                url = response.url
                if "v8back.icgoo.net/api/search/supplier" in url:
                    try:
                        text = await response.text()
                        if text.startswith("{") or text.startswith("["):
                            api_data["supplier"] = json.loads(text)
                    except Exception:
                        pass
                elif "v8back.icgoo.net/api/search/batch_price" in url:
                    try:
                        text = await response.text()
                        if text.startswith("{") or text.startswith("["):
                            api_data["price"] = json.loads(text)
                    except Exception:
                        pass

            page.on("response", capture_response)
            url = f"https://www.icgoo.net/search/{mpn}/1"
            await page.goto(url, timeout=30000)
            await page.wait_for_timeout(12000)

            # Try API-intercepted data first
            if api_data.get("supplier"):
                result = self._parse_supplier_api(mpn, api_data["supplier"], url)
                if result.status.value == "success":
                    # If no price from supplier API, try batch_price data
                    if result.price_unit is None and api_data.get("price"):
                        price = self._extract_batch_price(mpn, api_data["price"])
                        if price is not None:
                            result.price_unit = price
                    return result

            # Fallback: parse rendered DOM
            content = await page.content()
            return self._parse_dom(mpn, content, url)
        except Exception as e:
            logger.error(f"[ICGOO] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    def _parse_supplier_api(self, mpn: str, data: dict, url: str) -> PartResult:
        """Parse the supplier API JSON response."""
        try:
            # The API returns supplier offers with pricing
            items = None
            if isinstance(data, dict):
                items = (
                    data.get("data") or data.get("items") or data.get("list")
                    or data.get("results") or data.get("products")
                )
                # Handle nested: {data: {list: [...]}}
                if isinstance(items, dict):
                    items = items.get("list") or items.get("items") or items.get("data")
            if isinstance(data, list):
                items = data

            if not items or not isinstance(items, list):
                return self.not_found_result(mpn)

            # Get the first/best offer
            best = items[0] if items else None
            if not best or not isinstance(best, dict):
                return self.not_found_result(mpn)

            # Price extraction — try multiple field names
            price = (
                best.get("price") or best.get("unit_price") or best.get("unitPrice")
                or best.get("min_price") or best.get("minPrice")
                or best.get("cny_price") or best.get("sell_price")
                or best.get("ladder_price")
            )
            # Try nested price_breaks/ladder
            price_breaks = []
            ladder = best.get("price_breaks") or best.get("ladder") or best.get("prices") or []
            if isinstance(ladder, list):
                for item in ladder:
                    if isinstance(item, dict):
                        qty = item.get("qty") or item.get("quantity") or item.get("num")
                        p = item.get("price") or item.get("unit_price") or item.get("cny_price")
                        if qty and p:
                            price_breaks.append({"quantity": qty, "unit_price": p})
                if not price and price_breaks:
                    price = price_breaks[0]["unit_price"]

            return self.success_result(mpn, {
                "mpn": best.get("partno") or best.get("mpn") or best.get("goods_name") or mpn,
                "brand": (
                    best.get("mfr") or best.get("brand") or best.get("manufacturer")
                    or best.get("brand_name")
                ),
                "stock": (
                    best.get("stock") or best.get("inventory")
                    or best.get("qty") or best.get("available")
                ),
                "price_unit": price,
                "price_breaks": price_breaks,
                "moq": best.get("moq") or best.get("min_qty") or best.get("mpq"),
                "product_url": url,
                "description": best.get("desc") or best.get("description"),
            })
        except Exception as e:
            logger.warning(f"[ICGOO] API parse error: {e}")
            return self.not_found_result(mpn)

    def _extract_batch_price(self, mpn: str, data: Any) -> float | None:
        """Extract price from batch_price API response."""
        try:
            items = data if isinstance(data, list) else (
                data.get("data") or data.get("items") or data.get("list") or []
            )
            if isinstance(items, dict):
                items = items.get("data") or items.get("list") or []
            if not isinstance(items, list):
                return None

            mpn_norm = self._normalize_text(mpn)
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_mpn = item.get("partno") or item.get("mpn") or item.get("goods_name") or ""
                if mpn_norm not in self._normalize_text(str(item_mpn)):
                    continue
                # Try all price fields
                price = (
                    item.get("price") or item.get("unit_price") or item.get("cny_price")
                    or item.get("min_price") or item.get("sell_price")
                )
                if price:
                    try:
                        return float(price)
                    except (ValueError, TypeError):
                        pass
                # Try ladder
                ladder = item.get("ladder") or item.get("prices") or item.get("price_breaks") or []
                if isinstance(ladder, list) and ladder:
                    for lb in ladder:
                        if isinstance(lb, dict):
                            p = lb.get("price") or lb.get("unit_price") or lb.get("cny_price")
                            if p:
                                try:
                                    return float(p)
                                except (ValueError, TypeError):
                                    pass
        except Exception:
            pass
        return None

    def _parse_dom(self, mpn: str, html: str, url: str) -> PartResult:
        """Fallback: parse rendered DOM for product info."""
        import re

        mpn_norm = self._normalize_text(mpn)
        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        # ICGOO shows inquiry button (询价) rather than direct prices for many items
        has_inquiry = "询价" in html
        prices = re.findall(r'[￥¥$]\s*(\d+\.?\d*)', html)
        price_values = [float(p) for p in prices if 0.0001 < float(p) < 100000]

        result_data: dict[str, Any] = {
            "mpn": mpn,
            "product_url": url,
        }

        if price_values:
            result_data["price_unit"] = min(price_values)

        if has_inquiry and not price_values:
            result_data["description"] = "需询价"

        return self.success_result(mpn, result_data)
