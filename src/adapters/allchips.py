"""硬之城 (allchips.com) adapter — Playwright with captcha solving.

Captcha: YunZhiSuo (cdn.yzcstatic.com), mode=20 slider / mode=10 click.
Verified approach:
  - showCap() triggers captcha overlay
  - Canvas (390x200) contains background; .cap-slider-piece has puzzle piece
  - .cap-handle is the drag handle on .cap-track (390x44)
  - ddddocr.slide_match(piece, bg, simple_target=True) → target_x = drag distance
  - Success rate ~25-30% per attempt, 5 retries → ~80%+ overall
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import re
from typing import Any

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool
from src.core.captcha_solver import CaptchaSolver
from src.models import PartResult

logger = logging.getLogger(__name__)


@AdapterRegistry.register("allchips")
class AllchipsAdapter(BrowserAdapter):
    """硬之城 adapter — Playwright + ddddocr slider captcha solving."""

    SEARCH_URL = "https://www.allchips.com/search"
    MAX_CAPTCHA_RETRIES = 8

    def __init__(self, browser_pool: BrowserPool) -> None:
        super().__init__("硬之城", browser_pool)
        self._solver = CaptchaSolver()

    async def search_by_mpn(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            url = f"{self.SEARCH_URL}?keyword={mpn}"
            await page.goto(url, timeout=30000)
            await page.wait_for_timeout(6000)

            content = await page.content()

            if self._has_captcha(content):
                solved = await self._solve_with_retries(page, mpn)
                if not solved:
                    return self.failed_result(mpn, "验证码未通过(云之锁)")
                # Captcha JS does location.reload() after solve — wait for it
                await page.wait_for_timeout(4000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                content = await page.content()
                # If still captcha, try navigating back to search URL
                if self._has_captcha(content) and len(content) < 10000:
                    await page.goto(url, timeout=30000)
                    await page.wait_for_timeout(6000)
                    content = await page.content()
                    if self._has_captcha(content) and len(content) < 10000:
                        return self.failed_result(mpn, "验证码通过但cookie未持久化")

            return self._parse_results(mpn, content, url)
        except Exception as e:
            logger.error(f"[硬之城] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    def _has_captcha(self, html: str) -> bool:
        """Detect YunZhiSuo captcha by checking for its script/DOM markers."""
        indicators = ["captchabutton", "yzcstatic", "showcap"]
        html_lower = html.lower()
        return any(ind in html_lower for ind in indicators)

    async def _solve_with_retries(self, page, mpn: str) -> bool:
        """Trigger captcha panel and attempt solving with retries."""
        # Check if captcha is already visible (auto-triggered)
        images = await self._get_captcha_images(page)
        if not images:
            # Try triggering with showCap()
            has_showcap = await page.evaluate('typeof showCap === "function"')
            if has_showcap:
                await page.evaluate("showCap()")
                await page.wait_for_timeout(4000)
            else:
                # Try clicking the captcha button directly
                cap_btn = await page.query_selector('.captchabutton, [class*="captcha"]')
                if cap_btn:
                    await cap_btn.click()
                    await page.wait_for_timeout(4000)
                else:
                    logger.warning("[硬之城] showCap() not available and no captcha button")
                    return False

            # Get captcha images after trigger
            images = await self._get_captcha_images(page)
            if not images:
                logger.warning("[硬之城] Captcha images not available after trigger")
                return False

        bg_bytes, piece_bytes = images

        try:
            import ddddocr
            detector = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
        except ImportError:
            logger.error("[硬之城] ddddocr not installed")
            return False

        for attempt in range(1, self.MAX_CAPTCHA_RETRIES + 1):
            result = detector.slide_match(piece_bytes, bg_bytes, simple_target=True)
            gap_x = result.get("target_x", 0)
            confidence = result.get("confidence", 0)
            logger.info(
                f"[硬之城] Attempt {attempt}/{self.MAX_CAPTCHA_RETRIES}: "
                f"gap_x={gap_x}, conf={confidence:.2f}"
            )

            if gap_x <= 20:
                continue

            # Drag the handle
            handle = await page.query_selector(".cap-handle")
            if not handle:
                logger.warning("[硬之城] No .cap-handle found")
                return False
            hbox = await handle.bounding_box()
            if not hbox:
                return False

            sx = hbox["x"] + hbox["width"] / 2
            sy = hbox["y"] + hbox["height"] / 2

            # drag_distance = gap_x (verified by testing)
            trajectory = self._solver.generate_trajectory(gap_x)

            await page.mouse.move(sx, sy)
            await asyncio.sleep(random.uniform(0.2, 0.35))
            await page.mouse.down()
            await asyncio.sleep(random.uniform(0.08, 0.15))
            for x_offset, y_offset, dt in trajectory:
                await page.mouse.move(sx + x_offset, sy + y_offset)
                await asyncio.sleep(dt / 1000.0)
            await asyncio.sleep(random.uniform(0.05, 0.1))
            await page.mouse.up()

            await page.wait_for_timeout(3000)

            # Check if captcha track disappeared (= success)
            track = await page.query_selector(".cap-track")
            track_visible = False
            if track:
                try:
                    track_visible = await track.is_visible()
                except Exception:
                    pass

            if not track_visible:
                logger.info(f"[硬之城] Captcha SOLVED on attempt {attempt}")
                return True

            # Failed — get fresh images for next attempt
            logger.info(f"[硬之城] Attempt {attempt} failed, retrying...")
            await page.wait_for_timeout(2000)
            new_images = await self._get_captcha_images(page)
            if new_images:
                bg_bytes, piece_bytes = new_images

        return False

    async def _get_captcha_images(self, page) -> tuple[bytes, bytes] | None:
        """Extract canvas background and slider piece as bytes."""
        images = await page.evaluate("""() => {
            const canvas = document.querySelector('.cap-canvas-wrap canvas');
            const piece = document.querySelector('.cap-slider-piece');
            if (!canvas || !piece) return null;
            const bg = canvas.toDataURL('image/png');
            const ps = piece.src;
            if (!bg || !ps || !bg.includes(',') || !ps.includes(',')) return null;
            return { bg: bg, piece: ps };
        }""")
        if not images:
            return None
        try:
            bg_bytes = base64.b64decode(images["bg"].split(",")[1])
            piece_bytes = base64.b64decode(images["piece"].split(",")[1])
            return bg_bytes, piece_bytes
        except Exception:
            return None

    def _parse_results(self, mpn: str, html: str, url: str) -> PartResult:
        """Parse product data from allchips.com rendered HTML."""
        mpn_norm = self._normalize_text(mpn)

        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        prices = re.findall(r'[￥¥]\s*(\d+\.?\d*)', html)
        price_values = [float(p) for p in prices if 0.001 < float(p) < 100000]

        stock = None
        stock_match = re.search(r'(?:库存|stock|现货)[：:\s]*(\d[\d,]*)', html, re.I)
        if stock_match:
            stock = self._to_int(stock_match.group(1))

        # Brand: look for text content after "品牌" label inside elements
        brand = None
        brand_patterns = [
            r'品牌[^>]*>[^<]*<[^>]*>([A-Za-z][A-Za-z0-9\s\-&.]{1,40})<',
            r'manufacturer[^>]*>([A-Za-z][A-Za-z0-9\s\-&.]{1,40})<',
            r'brand-name[^>]*>([A-Za-z][A-Za-z0-9\s\-&.]{1,40})<',
        ]
        for pattern in brand_patterns:
            match = re.search(pattern, html, re.I)
            if match:
                brand = match.group(1).strip()
                break

        result_data: dict[str, Any] = {
            "mpn": mpn,
            "brand": brand,
            "stock": stock,
            "product_url": url,
        }

        if price_values:
            result_data["price_unit"] = min(price_values)

        return self.success_result(mpn, result_data)
