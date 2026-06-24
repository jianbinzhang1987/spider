"""Main entry point for the async component search system."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.browser_pool import BrowserPool
from src.models import PartResult

# Import all adapters to trigger registration
from src.adapters import oneyac, hqew, wlxmall, cmalls, icgoo, icstk, icdeal, allchips, ichunt, icnet, vipmro  # noqa: F401
from src.adapters import digikey, mouser, element14, lcsc, ickey  # noqa: F401
from src.adapters.registry import AdapterRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def search_all(mpn: str, adapters: list[str] | None = None) -> list[PartResult]:
    """Search for a part number across all (or specified) adapters."""
    pool = BrowserPool(max_pages=3, headless=True)
    await pool.start()

    results: list[PartResult] = []
    adapter_names = adapters or AdapterRegistry.list_adapters()

    tasks = []
    instances = []

    for name in adapter_names:
        adapter_cls = AdapterRegistry.get(name)
        if adapter_cls is None:
            logger.warning(f"Unknown adapter: {name}")
            continue

        # Determine how to instantiate
        from src.adapters.base import HttpAdapter, BrowserAdapter
        if issubclass(adapter_cls, HttpAdapter):
            instance = adapter_cls()
        elif issubclass(adapter_cls, BrowserAdapter):
            instance = adapter_cls(pool)
        else:
            continue

        instances.append(instance)
        tasks.append(instance.search_by_mpn(mpn))

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Convert exceptions to failed results
        final_results = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                adapter_name = adapter_names[i] if i < len(adapter_names) else "unknown"
                final_results.append(PartResult(
                    supplier=adapter_name,
                    query=mpn,
                    status="failed",
                    error_message=str(r),
                ))
            else:
                final_results.append(r)
        results = final_results

    # Cleanup
    for instance in instances:
        try:
            await instance.close()
        except Exception:
            pass
    await pool.stop()

    return results


async def main():
    """CLI entry point."""
    mpn = sys.argv[1] if len(sys.argv) > 1 else "STM32F103C8T6"
    adapters = sys.argv[2].split(",") if len(sys.argv) > 2 else None

    logger.info(f"Searching for: {mpn}")
    if adapters:
        logger.info(f"Using adapters: {adapters}")

    results = await search_all(mpn, adapters)

    print("\n" + "=" * 60)
    print(f"Search Results for: {mpn}")
    print("=" * 60)

    for r in results:
        print(f"\n--- {r.supplier} ---")
        print(f"  Status: {r.status}")
        if r.mpn:
            print(f"  MPN: {r.mpn}")
        if r.brand:
            print(f"  Brand: {r.brand}")
        if r.stock is not None:
            print(f"  Stock: {r.stock}")
        if r.price_unit is not None:
            print(f"  Price: ¥{r.price_unit}")
        if r.product_url:
            print(f"  URL: {r.product_url}")
        if r.error_message:
            print(f"  Error: {r.error_message}")

    # Output JSON
    output = [r.to_dict() for r in results]
    output_file = Path("search_results.json")
    output_file.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    logger.info(f"Results saved to {output_file}")


if __name__ == "__main__":
    asyncio.run(main())
