"""Base adapter classes for HTTP and Browser-based scrapers."""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from typing import Any

from src.models import PartResult, PriceBreak, QueryStatus, SearchType


class BaseAdapter(ABC):
    """Abstract base for all supplier adapters."""

    supplier_name: str

    def __init__(self, supplier_name: str) -> None:
        self.supplier_name = supplier_name

    @abstractmethod
    async def search_by_mpn(self, mpn: str) -> PartResult:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        pass

    def success_result(
        self,
        query: str,
        raw_data: dict[str, Any],
    ) -> PartResult:
        price_breaks = []
        for pb in raw_data.get("price_breaks") or []:
            if isinstance(pb, dict):
                qty = self._to_int(pb.get("quantity"))
                price = self._to_float(pb.get("unit_price") or pb.get("price"))
                if qty is not None and price is not None:
                    price_breaks.append(PriceBreak(quantity=qty, unit_price=price))

        return PartResult(
            supplier=self.supplier_name,
            query=query,
            status=QueryStatus.SUCCESS,
            mpn=raw_data.get("mpn"),
            sku=raw_data.get("sku"),
            brand=raw_data.get("brand"),
            package=raw_data.get("package"),
            description=raw_data.get("description"),
            stock=self._to_int(raw_data.get("stock")),
            moq=self._to_int(raw_data.get("moq")),
            price_unit=self._to_float(raw_data.get("price_unit")),
            price_currency=raw_data.get("price_currency", "CNY"),
            price_breaks=price_breaks,
            lead_time=raw_data.get("lead_time"),
            product_url=raw_data.get("product_url"),
            datasheet_url=raw_data.get("datasheet_url"),
        )

    def failed_result(self, query: str, error: str) -> PartResult:
        return PartResult(
            supplier=self.supplier_name,
            query=query,
            status=QueryStatus.FAILED,
            error_message=error,
        )

    def not_found_result(self, query: str) -> PartResult:
        return PartResult(
            supplier=self.supplier_name,
            query=query,
            status=QueryStatus.NOT_FOUND,
        )

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(float(str(value).replace(",", "").replace(" ", "")))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            cleaned = re.sub(r"[^\d.]", "", str(value))
            return float(cleaned) if cleaned else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _normalize_text(text: str) -> str:
        return "".join(
            c.lower() for c in text.strip() if c not in {" ", "-", "_", "/", "\\", "."}
        )


class HttpAdapter(BaseAdapter):
    """Base for adapters using curl_cffi HTTP requests."""

    def __init__(self, supplier_name: str, timeout: float = 15.0, min_interval: float = 1.0) -> None:
        super().__init__(supplier_name)
        self.timeout = timeout
        self.min_interval = min_interval
        self._client = None
        self._last_request_time: float = 0
        self._lock = asyncio.Lock()

    async def _rate_limit(self) -> None:
        import time
        now = time.monotonic()
        wait = self.min_interval - (now - self._last_request_time)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request_time = time.monotonic()

    def _get_client(self):
        if self._client is None:
            from curl_cffi.requests import AsyncSession
            self._client = AsyncSession(
                impersonate="chrome124",
                timeout=self.timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
        return self._client

    async def _fetch(self, url: str, **kwargs) -> Any:
        async with self._lock:
            await self._rate_limit()
            client = self._get_client()
            return await client.get(url, **kwargs)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


class BrowserAdapter(BaseAdapter):
    """Base for adapters using Playwright browser automation."""

    def __init__(self, supplier_name: str, browser_pool: "BrowserPool") -> None:
        super().__init__(supplier_name)
        self._pool = browser_pool

    async def _new_page(self):
        return await self._pool.acquire_page()

    async def _release_page(self, page) -> None:
        await self._pool.release_page(page)

    async def close(self) -> None:
        pass
