"""ICGOO (icgoo.net) adapter — Playwright with API response interception."""

from __future__ import annotations

import json
import logging
import re
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
                # Capture any API response from icgoo backend
                if "icgoo.net/api/" in url or "v8back.icgoo.net" in url:
                    try:
                        ct = response.headers.get("content-type", "")
                        if "json" not in ct and "text" not in ct:
                            return
                        text = await response.text()
                        if not text or (not text.startswith("{") and not text.startswith("[")):
                            return
                        data = json.loads(text)
                        if "supplier" in url or "search" in url:
                            api_data["supplier"] = data
                        if "price" in url or "batch" in url:
                            api_data["price"] = data
                        # Store all API responses for fallback
                        if "all" not in api_data:
                            api_data["all"] = []
                        api_data["all"].append(data)
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
                    # If still no price, try all captured API responses
                    if result.price_unit is None and api_data.get("all"):
                        for resp_data in api_data["all"]:
                            price = self._extract_batch_price(mpn, resp_data)
                            if price is not None:
                                result.price_unit = price
                                break
                    # If still no price, try DOM extraction
                    if result.price_unit is None:
                        content = await page.content()
                        dom_price = self._extract_price_from_dom(content)
                        if dom_price is not None:
                            result.price_unit = dom_price
                    return self._require_price(mpn, result)

            # Fallback: parse rendered DOM (try clicking into first product if available)
            content = await page.content()
            result = self._parse_dom(mpn, content, url)

            # If found but no price, try navigating to product detail page
            if result.status.value == "success" and result.price_unit is None:
                detail_url = await self._find_product_link(page, mpn)
                if detail_url:
                    await page.goto(detail_url, timeout=20000)
                    await page.wait_for_timeout(8000)
                    detail_content = await page.content()
                    detail_price = self._extract_price_from_dom(detail_content)
                    if detail_price is not None:
                        result.price_unit = detail_price
                        result.product_url = detail_url

            return self._require_price(mpn, result)
        except Exception as e:
            logger.error(f"[ICGOO] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    def _require_price(self, mpn: str, result: PartResult) -> PartResult:
        if result.status.value == "success" and result.price_unit is None and not result.price_breaks:
            return self.failed_result(mpn, "找到型号但未获取到报价")
        return result

    async def _find_product_link(self, page, mpn: str) -> str | None:
        """Try to find a product detail link on the search results page."""
        try:
            content = await page.content()
            # ICGOO product detail links: /part/xxxxx.html
            links = re.findall(r'href="(/part/[^"]+)"', content)
            if links:
                return f"https://www.icgoo.net{links[0]}"
            # Also try clicking on a product element
            product_el = await page.query_selector('a[href*="/part/"]')
            if product_el:
                href = await product_el.get_attribute("href")
                if href:
                    if href.startswith("/"):
                        return f"https://www.icgoo.net{href}"
                    return href
        except Exception:
            pass
        return None

    def _extract_price_from_dom(self, html: str) -> float | None:
        """Extract price from any page's DOM content."""
        price_values: list[float] = []

        # Pattern 1: Currency symbol followed by number
        prices = re.findall(r'[￥¥$]\s*(\d+\.?\d*)', html)
        price_values.extend(float(p) for p in prices if 0.0001 < float(p) < 100000)

        # Pattern 2: Price in data attributes or JSON embedded in page
        data_prices = re.findall(r'"(?:price|unitPrice|unit_price|sell_price|cny_price|min_price)"[:\s]*["\']?(\d+\.?\d*)', html)
        price_values.extend(float(p) for p in data_prices if 0.0001 < float(p) < 100000)

        # Pattern 3: Ladder price patterns (e.g., "1+ ¥0.05")
        ladder_prices = re.findall(r'\d+\+\s*[￥¥$]?\s*(\d+\.?\d+)', html)
        price_values.extend(float(p) for p in ladder_prices if 0.0001 < float(p) < 100000)

        # Pattern 4: class="price" or similar
        price_spans = re.findall(r'class="[^"]*price[^"]*"[^>]*>([^<]+)', html, re.I)
        for text in price_spans:
            nums = re.findall(r'(\d+\.?\d+)', text)
            for n in nums:
                v = float(n)
                if 0.0001 < v < 100000:
                    price_values.append(v)

        # Pattern 5: td/span with price-like content near "单价" or "价格"
        price_near_label = re.findall(r'(?:单价|价格|报价)[^<]{0,50}?(\d+\.?\d+)', html)
        price_values.extend(float(p) for p in price_near_label if 0.0001 < float(p) < 100000)

        return min(price_values) if price_values else None

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
        mpn_norm = self._normalize_text(mpn)
        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        # ICGOO shows inquiry button (询价) rather than direct prices for many items
        has_inquiry = "询价" in html

        # Extract price from DOM
        price = self._extract_price_from_dom(html)

        result_data: dict[str, Any] = {
            "mpn": mpn,
            "product_url": url,
        }

        if price is not None:
            result_data["price_unit"] = price

        if has_inquiry and price is None:
            result_data["description"] = "需询价"

        return self.success_result(mpn, result_data)
