"""Batch search scheduler — orchestrates parallel queries across all adapters.

Handles:
- Concurrent adapter execution with semaphore control
- USD/CNY exchange rate fetching (cached)
- Price break matching by quantity
- Best price identification across suppliers
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.adapters.base import BrowserAdapter, HttpAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool
from src.models import PartResult, PriceBreak, QueryStatus

logger = logging.getLogger(__name__)


@dataclass
class SearchItem:
    """A single item from the uploaded Excel."""
    mpn: str
    brand: str = ""
    quantity: int = 1
    row_index: int = 0


@dataclass
class SearchResultRow:
    """A single result row for the output Excel."""
    mpn: str
    brand: str
    quantity: int
    supplier: str
    price_cny: float | None = None
    price_original: str | None = None
    stock: int | None = None
    lead_time: str | None = None
    product_url: str | None = None
    query_time: str | None = None
    is_best_price: bool = False
    status: str = "success"
    error: str | None = None


@dataclass
class TaskProgress:
    """Progress tracking for a search task."""
    total_items: int = 0
    completed_items: int = 0
    total_queries: int = 0
    completed_queries: int = 0
    current_item: str = ""
    status: str = "pending"  # pending, running, completed, failed
    results: list[SearchResultRow] = field(default_factory=list)


class BatchScheduler:
    """Orchestrates parallel searches across all adapters."""

    def __init__(
        self,
        adapter_names: list[str] | None = None,
        max_concurrent: int = 5,
        exchange_rate: float | None = None,
    ) -> None:
        self._adapter_names = adapter_names or AdapterRegistry.list_adapters()
        self._max_concurrent = max_concurrent
        self._exchange_rate = exchange_rate
        self._pool: BrowserPool | None = None
        self._progress_callback: Callable[[TaskProgress], None] | None = None

    async def _get_exchange_rate(self) -> float:
        """Get USD/CNY exchange rate (cached)."""
        if self._exchange_rate:
            return self._exchange_rate

        try:
            from curl_cffi.requests import AsyncSession
            async with AsyncSession() as session:
                resp = await session.get(
                    "https://open.er-api.com/v6/latest/USD",
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    rate = data.get("rates", {}).get("CNY", 7.25)
                    self._exchange_rate = rate
                    logger.info(f"Exchange rate USD/CNY: {rate}")
                    return rate
        except Exception as e:
            logger.warning(f"Failed to fetch exchange rate: {e}, using default 7.25")

        self._exchange_rate = 7.25
        return self._exchange_rate

    def _match_price_by_quantity(
        self, price_breaks: list[PriceBreak], quantity: int
    ) -> float | None:
        """Find the matching price tier for the given quantity."""
        if not price_breaks:
            return None

        sorted_breaks = sorted(price_breaks, key=lambda pb: pb.quantity)
        matched_price = sorted_breaks[0].unit_price

        for pb in sorted_breaks:
            if pb.quantity <= quantity:
                matched_price = pb.unit_price
            else:
                break

        return matched_price

    def _is_usd_supplier(self, supplier: str) -> bool:
        """Determine if a supplier uses USD pricing."""
        usd_suppliers = {"Digi-Key", "Mouser", "element14"}
        return supplier in usd_suppliers

    async def search_single_item(
        self,
        item: SearchItem,
        progress: TaskProgress,
    ) -> list[SearchResultRow]:
        """Search a single MPN across all adapters."""
        results: list[SearchResultRow] = []
        exchange_rate = await self._get_exchange_rate()

        semaphore = asyncio.Semaphore(self._max_concurrent)
        instances: list = []

        async def search_one_adapter(adapter_name: str) -> SearchResultRow | None:
            async with semaphore:
                adapter_cls = AdapterRegistry.get(adapter_name)
                if adapter_cls is None:
                    return None

                try:
                    if issubclass(adapter_cls, HttpAdapter):
                        instance = adapter_cls()
                    elif issubclass(adapter_cls, BrowserAdapter):
                        if self._pool is None:
                            return None
                        instance = adapter_cls(self._pool)
                    else:
                        return None

                    instances.append(instance)
                    result = await instance.search_by_mpn(item.mpn)

                    row = self._convert_to_row(item, result, exchange_rate)
                    progress.completed_queries += 1
                    return row
                except Exception as e:
                    logger.error(f"[{adapter_name}] Error: {e}")
                    progress.completed_queries += 1
                    return SearchResultRow(
                        mpn=item.mpn,
                        brand=item.brand,
                        quantity=item.quantity,
                        supplier=adapter_name,
                        status="failed",
                        error=str(e),
                    )

        tasks = [search_one_adapter(name) for name in self._adapter_names]
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in task_results:
            if isinstance(r, SearchResultRow):
                results.append(r)
            elif isinstance(r, Exception):
                logger.error(f"Unexpected error: {r}")

        # Cleanup instances
        for inst in instances:
            try:
                await inst.close()
            except Exception:
                pass

        # Mark best price
        self._mark_best_price(results)

        progress.completed_items += 1
        progress.current_item = item.mpn
        return results

    def _convert_to_row(
        self, item: SearchItem, result: PartResult, exchange_rate: float
    ) -> SearchResultRow:
        """Convert a PartResult to a SearchResultRow with price conversion."""
        from datetime import datetime, timezone

        row = SearchResultRow(
            mpn=item.mpn,
            brand=item.brand,
            quantity=item.quantity,
            supplier=result.supplier,
            stock=result.stock,
            lead_time=result.lead_time or "未显示",
            product_url=result.product_url,
            query_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            status=result.status.value if isinstance(result.status, QueryStatus) else str(result.status),
            error=result.error_message,
        )

        if result.status != QueryStatus.SUCCESS:
            return row

        # Get price matching the quantity
        price = self._match_price_by_quantity(result.price_breaks, item.quantity)
        if price is None:
            price = result.price_unit

        if price is not None:
            if self._is_usd_supplier(result.supplier):
                row.price_cny = round(price * exchange_rate, 4)
                row.price_original = f"${price} USD"
            else:
                row.price_cny = price
                row.price_original = f"¥{price} CNY"

        return row

    def _mark_best_price(self, results: list[SearchResultRow]) -> None:
        """Mark the row with the lowest CNY price."""
        priced_rows = [r for r in results if r.price_cny is not None and r.price_cny > 0]
        if not priced_rows:
            return

        best = min(priced_rows, key=lambda r: r.price_cny)
        best.is_best_price = True

    async def run(
        self,
        items: list[SearchItem],
        progress: TaskProgress,
        use_browser: bool = True,
    ) -> list[SearchResultRow]:
        """Run batch search for all items."""
        progress.total_items = len(items)
        progress.total_queries = len(items) * len(self._adapter_names)
        progress.status = "running"

        if use_browser:
            self._pool = BrowserPool(max_pages=3, headless=True)
            await self._pool.start()

        all_results: list[SearchResultRow] = []

        try:
            for item in items:
                progress.current_item = item.mpn
                item_results = await self.search_single_item(item, progress)
                all_results.extend(item_results)

                if self._progress_callback:
                    self._progress_callback(progress)
        finally:
            if self._pool:
                await self._pool.stop()
                self._pool = None

        progress.status = "completed"
        progress.results = all_results
        return all_results
