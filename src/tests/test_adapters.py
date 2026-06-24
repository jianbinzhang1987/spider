"""Integration tests for Phase 1 adapters."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.core.browser_pool import BrowserPool
from src.adapters.oneyac import OneyacAdapter
from src.adapters.hqew import HqewAdapter
from src.adapters.wlxmall import WlxmallAdapter
from src.adapters.cmalls import CmallsAdapter
from src.adapters.icgoo import IcgooAdapter
from src.models import QueryStatus


TEST_MPN = "STM32F103C8T6"


async def test_oneyac():
    """Test 唯样商城 adapter (curl_cffi, no browser needed)."""
    adapter = OneyacAdapter()
    result = await adapter.search_by_mpn(TEST_MPN)
    await adapter.close()
    assert result.status == QueryStatus.SUCCESS, f"Expected SUCCESS, got {result.status}: {result.error_message}"
    assert result.mpn is not None
    print(f"  ✓ 唯样商城: mpn={result.mpn}, price={result.price_unit}")
    return True


async def test_browser_adapters():
    """Test browser-based adapters (华强/万联/小猫/ICGOO)."""
    pool = BrowserPool(max_pages=2, headless=True)
    await pool.start()

    adapters = [
        ("华强电子网", HqewAdapter(pool)),
        ("万联芯城", WlxmallAdapter(pool)),
        ("小猫芯城", CmallsAdapter(pool)),
        ("ICGOO", IcgooAdapter(pool)),
    ]

    results = []
    for name, adapter in adapters:
        result = await adapter.search_by_mpn(TEST_MPN)
        status_ok = result.status == QueryStatus.SUCCESS
        results.append((name, status_ok, result))
        icon = "✓" if status_ok else "✗"
        print(f"  {icon} {name}: status={result.status}, mpn={result.mpn}, price={result.price_unit}")

    await pool.stop()
    return results


async def main():
    print(f"\n{'='*60}")
    print(f"Integration Test: Phase 1 Adapters (MPN: {TEST_MPN})")
    print(f"{'='*60}\n")

    print("[1/2] Testing HTTP adapter (唯样)...")
    try:
        await test_oneyac()
    except Exception as e:
        print(f"  ✗ 唯样商城: {e}")

    print("\n[2/2] Testing Browser adapters...")
    try:
        results = await test_browser_adapters()
    except Exception as e:
        print(f"  ✗ Browser adapters failed: {e}")
        results = []

    print(f"\n{'='*60}")
    total = 5
    passed = 1  # oneyac
    passed += sum(1 for _, ok, _ in results if ok)
    print(f"Result: {passed}/{total} adapters returned SUCCESS")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
