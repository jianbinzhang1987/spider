"""Manual verification preflight for captcha/login-heavy adapters.

Run this before large batches so normal searches can stay headless:

    python src/preflight_verification.py RC0402FR-0710KL icdeal,allchips,icgoo
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adapters import allchips, digikey, icdeal, icgoo, icnet, mouser  # noqa: F401
from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_ADAPTERS = ["icdeal", "allchips", "icgoo", "icnet", "digikey", "mouser"]
SESSION_PATHS = {
    "icdeal": "data/sessions/icdeal.json",
    "allchips": "data/sessions/allchips.json",
    "icgoo": "data/sessions/icgoo.json",
    "icnet": "data/sessions/icnet.json",
    "digikey": "data/sessions/digikey.json",
    "mouser": "data/sessions/mouser.json",
}


async def preflight(mpn: str, adapter_names: list[str]) -> None:
    """Open one visible browser per adapter and persist its verification state."""
    for adapter_name in adapter_names:
        adapter_cls = AdapterRegistry.get(adapter_name)
        if adapter_cls is None:
            print(f"[跳过] 未知适配器: {adapter_name}")
            continue
        if not issubclass(adapter_cls, BrowserAdapter):
            print(f"[跳过] {adapter_name} 不需要浏览器验证")
            continue

        session_path = SESSION_PATHS.get(adapter_name, f"data/sessions/{adapter_name}.json")
        print(f"\n=== {adapter_name} 验证预热 ===")
        print(f"将打开可见浏览器。请手动完成登录/验证码；完成后程序会继续。")
        print(f"会话将保存到: {session_path}")

        pool = BrowserPool(max_pages=1, headless=False, storage_state_path=session_path)
        await pool.start()
        try:
            adapter = adapter_cls(pool)
            result = await adapter.search_by_mpn(mpn)
            print(f"结果: {result.status.value}")
            if result.error_message:
                print(f"说明: {result.error_message}")
            if result.price_unit is not None:
                print(f"价格: {result.price_unit}")
        except Exception as e:
            logger.exception("[%s] preflight failed", adapter_name)
            print(f"失败: {e}")
        finally:
            await pool.stop()

        _merge_browser_sessions()


def _merge_browser_sessions() -> None:
    """Merge per-site Playwright storage states for mixed browser batches."""
    merged = {"cookies": [], "origins": []}
    seen_cookies = set()
    seen_origins = set()

    for session_path in SESSION_PATHS.values():
        path = Path(session_path)
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        for cookie in data.get("cookies") or []:
            key = (
                cookie.get("name"),
                cookie.get("domain"),
                cookie.get("path"),
            )
            if key not in seen_cookies:
                seen_cookies.add(key)
                merged["cookies"].append(cookie)

        for origin in data.get("origins") or []:
            key = origin.get("origin")
            if key and key not in seen_origins:
                seen_origins.add(key)
                merged["origins"].append(origin)

    output_path = Path("data/sessions/browser_state.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已合并批量浏览器会话: {output_path}")


def _parse_adapters(raw: str | None) -> list[str]:
    if not raw:
        return DEFAULT_ADAPTERS
    return [item.strip() for item in raw.split(",") if item.strip()]


async def main() -> None:
    mpn = sys.argv[1] if len(sys.argv) > 1 else "RC0402FR-0710KL"
    adapters = _parse_adapters(sys.argv[2] if len(sys.argv) > 2 else None)
    await preflight(mpn, adapters)


if __name__ == "__main__":
    asyncio.run(main())
