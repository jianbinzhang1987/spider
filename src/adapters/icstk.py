"""艾汐芯城 (ic-stk.cn) adapter — Playwright rendering."""

from __future__ import annotations

import asyncio
import json
import re
import logging
from typing import Any

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool
from src.models import PartResult

logger = logging.getLogger(__name__)


@AdapterRegistry.register("icstk")
class IcstkAdapter(BrowserAdapter):
    """
    艾汐芯城 adapter.

    Strategy: Playwright renders the search route and captures Product/AjaxSearch
    JSON responses. Bare HTTP POST returns "检测验证，无权限访问", so requests must
    run inside a browser session.
    """

    def __init__(self, browser_pool: BrowserPool) -> None:
        super().__init__("艾汐芯城", browser_pool)

    async def search_by_mpn(self, mpn: str) -> PartResult:
        page = await self._new_page()
        api_payloads: list[dict[str, Any]] = []
        response_tasks: set[asyncio.Task] = set()

        async def capture_response(response) -> None:
            if "Product/AjaxSearch" not in response.url:
                return
            try:
                payload = await response.json()
            except Exception:
                return
            if payload.get("state") == "success":
                api_payloads.append(payload)

        def on_response(response) -> None:
            task = asyncio.create_task(capture_response(response))
            response_tasks.add(task)
            task.add_done_callback(response_tasks.discard)

        page.on("response", on_response)
        try:
            url = f"https://www.ic-stk.cn/search/{mpn}.html"
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Detect geo-block
            if response and response.status in (403, 493, 503):
                return self.failed_result(mpn, f"HTTP {response.status} - 可能需要国内IP")

            await page.wait_for_timeout(8000)
            if response_tasks:
                await asyncio.gather(*response_tasks, return_exceptions=True)

            content = await page.content()

            # Check for anti-bot page
            if any(kw in content for kw in ["访问受限", "Access Denied", "检测验证", "crawler_img"]):
                return self.failed_result(mpn, "WAF拦截 - 需要浏览器会话或人工验证")

            result = self._parse_api_payloads(mpn, api_payloads, url)
            if result.status.value != "not_found":
                return result

            return self._parse_results(mpn, content, url)
        except Exception as e:
            logger.error(f"[艾汐芯城] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            page.remove_listener("response", on_response)
            await self._release_page(page)

    def _parse_api_payloads(self, mpn: str, payloads: list[dict[str, Any]], url: str) -> PartResult:
        """Parse Product/AjaxSearch JSON payloads captured in the browser session."""
        products: list[dict[str, Any]] = []
        for payload in payloads:
            data = payload.get("data") or {}
            products.extend(self._extract_products(data.get("ProductList")))
            for channel in data.get("ChannelList") or []:
                if isinstance(channel, dict):
                    products.extend(self._extract_products(channel.get("ProductList"), channel))

        if not products:
            return self.not_found_result(mpn)

        mpn_norm = self._normalize_text(mpn)
        candidates = [
            product for product in products
            if self._normalize_text(str(product.get("Model") or product.get("PartNo") or "")) == mpn_norm
        ]
        if not candidates:
            candidates = [
                product for product in products
                if mpn_norm in self._normalize_text(str(product.get("Model") or product.get("Description") or ""))
            ]
        if not candidates:
            return self.not_found_result(mpn)

        best = sorted(candidates, key=self._product_rank_key)[0]
        price_breaks = self._parse_price_breaks(best)
        price_unit = (
            best.get("LowestPrice")
            or best.get("OLowestPrice")
            or best.get("OrderPrice")
            or (price_breaks[0]["unit_price"] if price_breaks else None)
        )

        detail_url = best.get("DetailUrl")
        if detail_url and detail_url.startswith("/"):
            detail_url = f"https://www.ic-stk.cn{detail_url}"

        return self.success_result(mpn, {
            "mpn": best.get("Model") or mpn,
            "brand": best.get("Brand"),
            "package": best.get("Package"),
            "description": best.get("Description"),
            "stock": best.get("InvQty"),
            "moq": best.get("MOQ") or best.get("OrderQty"),
            "price_unit": price_unit,
            "price_breaks": price_breaks,
            "lead_time": best.get("Delivery") or best.get("LeadTime") or best.get("ShipTo"),
            "product_url": detail_url or url,
            "datasheet_url": best.get("PdfUrl"),
        })

    def _extract_products(
        self,
        products: Any,
        channel: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(products, list):
            return []
        extracted = []
        for product in products:
            if isinstance(product, dict):
                merged = dict(product)
                if channel:
                    merged.setdefault("Supplier", channel.get("ChannelName"))
                    merged.setdefault("ChannelName", channel.get("ChannelName"))
                extracted.append(merged)
        return extracted

    def _product_rank_key(self, product: dict[str, Any]) -> tuple[bool, float]:
        price = (
            self._to_float(product.get("LowestPrice"))
            or self._to_float(product.get("OLowestPrice"))
            or self._to_float(product.get("OrderPrice"))
        )
        if price is None:
            breaks = self._parse_price_breaks(product)
            price = breaks[0]["unit_price"] if breaks else None
        return (price is None, price or 10**12)

    def _parse_price_breaks(self, product: dict[str, Any]) -> list[dict[str, float | int]]:
        breaks: list[dict[str, float | int]] = []
        ladders = product.get("LadderModelList")
        if isinstance(ladders, list):
            for item in ladders:
                if not isinstance(item, dict):
                    continue
                qty = self._to_int(item.get("Qty") or item.get("quantity"))
                price = self._to_float(item.get("Price") or item.get("price"))
                if qty is not None and price is not None:
                    breaks.append({"quantity": qty, "unit_price": price})

        price_range = product.get("PriceRange")
        if price_range and not breaks:
            try:
                parsed = json.loads(price_range)
            except (TypeError, json.JSONDecodeError):
                parsed = []
            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    qty = self._to_int(item.get("Qty") or item.get("quantity"))
                    price = self._to_float(item.get("Price") or item.get("price"))
                    if qty is not None and price is not None:
                        breaks.append({"quantity": qty, "unit_price": price})

        return sorted(breaks, key=lambda item: int(item["quantity"]))

    def _parse_results(self, mpn: str, html: str, url: str) -> PartResult:
        """Parse product data from rendered HTML."""
        mpn_norm = self._normalize_text(mpn)

        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        prices = re.findall(r'[￥¥$]\s*(\d+\.?\d*)', html)
        price_values = [float(p) for p in prices if 0.0001 < float(p) < 100000]

        stock = None
        stock_match = re.search(r'(?:库存|stock|现货)[：:\s]*(\d[\d,]*)', html, re.I)
        if stock_match:
            stock = self._to_int(stock_match.group(1))

        brand = None
        brand_match = re.search(r'(?:品牌|brand|厂商)[：:\s]*([^<\s]{2,30})', html, re.I)
        if brand_match:
            brand = brand_match.group(1)

        result_data = {
            "mpn": mpn,
            "brand": brand,
            "stock": stock,
            "product_url": url,
        }

        if price_values:
            result_data["price_unit"] = min(price_values)

        return self.success_result(mpn, result_data)
