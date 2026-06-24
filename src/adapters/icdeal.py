"""百能云芯 (icdeal.com) adapter — Playwright rendering."""

from __future__ import annotations

import asyncio
import re
import logging

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool
from src.models import PartResult

logger = logging.getLogger(__name__)


@AdapterRegistry.register("icdeal")
class IcdealAdapter(BrowserAdapter):
    """
    百能云芯 adapter.

    Strategy: Playwright opens the real search route and reports WAF slider
    verification explicitly. Fully automated search is not reliable until an
    official interface or a manually verified browser session is available.
    """

    SEARCH_URL = "https://www.icdeal.com/s/{mpn}/"
    MANUAL_VERIFY_TIMEOUT_SECONDS = 180

    def __init__(self, browser_pool: BrowserPool, manual_verify: bool = True) -> None:
        super().__init__("百能云芯", browser_pool)
        self._manual_verify = manual_verify

    async def search_by_mpn(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            url = self.SEARCH_URL.format(mpn=mpn)
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Detect geo-block or WAF
            if response and response.status in (403, 493, 503):
                return self.failed_result(mpn, f"HTTP {response.status} - 可能需要国内IP")

            await page.wait_for_timeout(10000)

            content = await page.content()
            current_url = page.url

            # Check for WAF/block page indicators
            if self._is_waf_page(current_url, content):
                if not self._manual_verify:
                    return self.failed_result(mpn, "WAF滑块验证，需要人工验证、持久化会话或官方接口")
                if self._pool.headless:
                    return self.failed_result(mpn, "WAF滑块验证需要可见浏览器，请关闭 headless 后重试")
                verified = await self._wait_for_manual_verification(page, url)
                if not verified:
                    return self.failed_result(mpn, "等待人工 WAF 验证超时")
                await page.wait_for_timeout(5000)
                content = await page.content()
                current_url = page.url
                if self._is_waf_page(current_url, content):
                    return self.failed_result(mpn, "WAF滑块验证未通过")
                if self._normalize_text(mpn) not in self._normalize_text(content):
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(8000)
                    content = await page.content()
                    current_url = page.url
                    if self._is_waf_page(current_url, content):
                        return self.failed_result(mpn, "WAF滑块验证后再次触发验证")

            return self._parse_results(mpn, content, current_url)
        except Exception as e:
            logger.error(f"[百能云芯] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    def _is_waf_page(self, url: str, html: str) -> bool:
        """Detect icdeal WAF verification pages."""
        waf_signals = [
            "waf.icdeal.com/waf/verification",
            "正在验证您的身份",
            "滑动验证码",
            "showCap",
            "captcha",
            "访问受限",
            "Access Denied",
        ]
        target = f"{url}\n{html}"
        return any(signal in target for signal in waf_signals)

    async def _wait_for_manual_verification(self, page, target_url: str) -> bool:
        """Wait for the user to complete the visible WAF slider challenge."""
        logger.warning(
            "[百能云芯] 已弹出浏览器，请手动完成滑块验证。验证通过后程序会自动继续。"
        )
        try:
            await page.bring_to_front()
        except Exception:
            pass

        deadline = asyncio.get_running_loop().time() + self.MANUAL_VERIFY_TIMEOUT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            await page.wait_for_timeout(2000)
            html = await page.content()
            if not self._is_waf_page(page.url, html):
                return True

            # Some WAF pages clear the challenge but do not redirect reliably.
            body_text = ""
            try:
                body_text = await page.locator("body").inner_text(timeout=1000)
            except Exception:
                pass
            if "验证成功" in body_text or "success" in body_text.lower():
                try:
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                    return True
                except Exception:
                    return False

        return False

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
