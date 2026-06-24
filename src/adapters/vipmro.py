"""京东工品汇 (vipmro.com) adapter — HTTP JSON API."""

from __future__ import annotations

import logging
from typing import Any

from src.adapters.base import HttpAdapter
from src.adapters.registry import AdapterRegistry
from src.models import PartResult

logger = logging.getLogger(__name__)


@AdapterRegistry.register("vipmro")
class VipmroAdapter(HttpAdapter):
    """
    京东工品汇 adapter.

    Strategy: call the JSON search API directly. Some overseas networks may still
    receive JD Cloud HTTP 493 geo blocks, so keep explicit detection for that case.
    """

    SEARCH_API = "https://www.vipmro.com/interface1/goods/search/mall/v2/1/20"

    def __init__(self, timeout: float = 20.0, min_interval: float = 1.5) -> None:
        super().__init__("京东工品汇", timeout=timeout, min_interval=min_interval)

    async def search_by_mpn(self, mpn: str) -> PartResult:
        try:
            response = await self._fetch(
                self.SEARCH_API,
                params={
                    "deliveryTime": "",
                    "isSop": "0",
                    "categoryId": "",
                    "attrValueIds": "",
                    "keyword": mpn,
                    "sortFields": "",
                    "sortFlags": "",
                    "stock": "",
                    "stockNew": "",
                    "range": "",
                    "gradeType": "",
                    "searchType": "3",
                    "platform": "2",
                    "channel": "2",
                },
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": f"https://www.vipmro.com/ss?keyword={mpn}",
                },
            )

            if response.status_code == 493:
                return self.failed_result(mpn, "IP地域限制(HTTP 493)，需国内IP")
            if response.status_code != 200:
                return self.failed_result(mpn, f"HTTP {response.status_code}")

            if "deny:geo" in str(response.headers).lower():
                return self.failed_result(mpn, "IP地域限制(x-jfe-reason: deny:geo)，需国内IP")

            return self._parse_json(mpn, response.json())
        except Exception as e:
            logger.error(f"[京东工品汇] search failed: {e}")
            return self.failed_result(mpn, str(e))

    def _parse_json(self, mpn: str, data: dict[str, Any]) -> PartResult:
        """Parse product data from the search API JSON."""
        if data.get("code") != 0:
            return self.failed_result(mpn, data.get("msg") or "接口返回失败")

        products = (data.get("data") or {}).get("goodsList") or []
        if not products:
            return self.not_found_result(mpn)

        mpn_norm = self._normalize_text(mpn)
        exact_matches = [
            item for item in products
            if self._normalize_text(str(item.get("model") or "")) == mpn_norm
        ]
        candidates = exact_matches or [
            item for item in products
            if mpn_norm in self._normalize_text(str(item.get("model") or item.get("goodsName") or ""))
        ]
        if not candidates:
            return self.not_found_result(mpn)

        best = sorted(
            candidates,
            key=lambda item: (
                self._to_float(item.get("salePrice") or item.get("showPrice") or item.get("finalPrice")) is None,
                self._to_float(item.get("salePrice") or item.get("showPrice") or item.get("finalPrice")) or 10**12,
            ),
        )[0]

        result_data = {
            "mpn": best.get("model") or mpn,
            "sku": best.get("buyNo") or best.get("goodsNo") or best.get("id"),
            "brand": best.get("brandName") or (best.get("goodsName") or "").split(" ", 1)[0],
            "stock": best.get("stock"),
            "moq": best.get("orderQuantity") or best.get("batchQuantity"),
            "price_unit": best.get("salePrice") or best.get("showPrice") or best.get("finalPrice"),
            "lead_time": self._format_lead_time(best),
            "description": best.get("goodsName"),
            "datasheet_url": best.get("sjsc"),
            "product_url": f"https://www.vipmro.com/ss?keyword={mpn}",
        }

        return self.success_result(mpn, result_data)

    def _format_lead_time(self, item: dict[str, Any]) -> str | None:
        delivery_time = item.get("deliveryTime")
        if delivery_time in (None, ""):
            return None
        return f"{delivery_time}日发货"
