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

            # Set realistic browser headers to reduce WAF triggering
            await page.set_extra_http_headers({
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })

            response = await self._goto_with_retry(page, url)

            # Detect geo-block or WAF
            if response and response.status in (403, 493, 503):
                return self.failed_result(mpn, f"HTTP {response.status} - 可能需要国内IP")

            # Capture content quickly (before any JS WAF redirect kicks in)
            # Wait just enough for Vue/React to render product data
            await page.wait_for_timeout(5000)
            content = await page.content()
            current_url = page.url

            # If WAF already redirected, try to get data from what we have
            if self._is_waf_page(current_url, content):
                # In headless mode, retry with a shorter wait
                if self._pool.headless:
                    try:
                        await self._goto_with_retry(page, url)
                        # Grab immediately after DOM is ready
                        await page.wait_for_timeout(3000)
                        content = await page.content()
                        current_url = page.url
                    except Exception:
                        pass

                    if self._is_waf_page(current_url, content):
                        return self.failed_result(mpn, "WAF滑块验证，headless模式无法通过")
                elif self._manual_verify:
                    verified = await self._wait_for_manual_verification(page, url)
                    if not verified:
                        return self.failed_result(mpn, "等待人工 WAF 验证超时")
                    await page.wait_for_timeout(5000)
                    content = await page.content()
                    current_url = page.url
                    if self._is_waf_page(current_url, content):
                        return self.failed_result(mpn, "WAF滑块验证未通过")
                    if self._normalize_text(mpn) not in self._normalize_text(content):
                        await self._goto_with_retry(page, url)
                        await page.wait_for_timeout(8000)
                        content = await page.content()
                        current_url = page.url
                        if self._is_waf_page(current_url, content):
                            return self.failed_result(mpn, "WAF滑块验证后再次触发验证")
                else:
                    return self.failed_result(mpn, "WAF滑块验证，需要人工验证、持久化会话或官方接口")

            # Try parsing body text first
            try:
                body_text = await page.locator("body").inner_text(timeout=5000)
                text_result = self._parse_text_results(mpn, body_text, current_url)
                if text_result.status.value != "not_found":
                    return text_result
            except Exception:
                pass

            # Fallback: parse raw HTML for prices
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
            "访问受限",
            "Access Denied",
        ]
        target = f"{url}\n{html}"
        return any(signal in target for signal in waf_signals)

    def _parse_text_results(self, mpn: str, text: str, url: str) -> PartResult:
        """Parse icdeal rendered result text by product card blocks."""
        mpn_norm = self._normalize_text(mpn)
        if mpn_norm not in self._normalize_text(text):
            return self.not_found_result(mpn)

        blocks = []
        # Try multiple marker patterns (text rendering may vary)
        markers = [f"数据手册\n{mpn}", f"手册\n{mpn}", mpn]
        for marker in markers:
            if marker in text:
                for chunk in text.split(marker)[1:]:
                    block = f"{mpn}{chunk.split('官方客服电话', 1)[0]}"
                    if ("品牌" in block or "库存" in block or "¥" in block):
                        blocks.append(block)
                if blocks:
                    break

        if not blocks:
            return self.not_found_result(mpn)

        parsed = [self._parse_product_block(mpn, block) for block in blocks]
        parsed = [item for item in parsed if item]
        if not parsed:
            return self.not_found_result(mpn)

        best = sorted(
            parsed,
            key=lambda item: (
                item.get("price_unit") is None,
                item.get("price_unit") or 10**12,
            ),
        )[0]
        best["product_url"] = url
        return self.success_result(mpn, best)

    def _parse_product_block(self, mpn: str, block: str) -> dict | None:
        brand = self._match_label(block, "品牌")
        package = self._match_label(block, "封装")
        stock = self._match_label(block, "库存")
        moq = self._match_label(block, "标准包")
        lead_time = None
        lead_time_match = re.search(r"大陆[:：]\s*([^\n]+)", block)
        if lead_time_match:
            lead_time = lead_time_match.group(1).strip()

        prices = [
            float(price)
            for price in re.findall(r"¥\s*(\d+\.?\d*)", block)
            if 0.0001 < float(price) < 100000
        ]
        if not prices and not brand:
            return None

        price_breaks = []
        for qty, price in re.findall(r"(\d+)\+\s*(?:\n+\s*--)?\s*\n+\s*¥\s*(\d+\.?\d*)", block):
            qty_int = self._to_int(qty)
            price_float = self._to_float(price)
            if qty_int is not None and price_float is not None:
                price_breaks.append({"quantity": qty_int, "unit_price": price_float})

        return {
            "mpn": mpn,
            "brand": brand,
            "package": package,
            "stock": stock,
            "moq": moq,
            "price_unit": min(prices) if prices else None,
            "price_breaks": price_breaks,
            "lead_time": lead_time,
        }

    def _match_label(self, text: str, label: str) -> str | None:
        # Try full-width colon, half-width colon, or just the label
        match = re.search(rf"{label}[：:]\s*\n?\s*([^\n]+)", text)
        if not match:
            return None
        return match.group(1).strip()

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

            if "waf.icdeal.com/waf/verification" in page.url and "403 Forbidden" in html:
                try:
                    await self._goto_with_retry(page, target_url)
                    await page.wait_for_timeout(3000)
                    html = await page.content()
                    if not self._is_waf_page(page.url, html):
                        return True
                except Exception:
                    pass

            # Some WAF pages clear the challenge but do not redirect reliably.
            body_text = ""
            try:
                body_text = await page.locator("body").inner_text(timeout=1000)
            except Exception:
                pass
            if "验证成功" in body_text or "success" in body_text.lower():
                try:
                    await self._goto_with_retry(page, target_url)
                    return True
                except Exception:
                    return False

        return False

    async def _goto_with_retry(self, page, url: str):
        """Navigate to icdeal with a softer retry for transient WAF/network aborts."""
        last_error: Exception | None = None
        for attempt, wait_until in enumerate(("domcontentloaded", "commit", "domcontentloaded"), start=1):
            try:
                response = await page.goto(url, wait_until=wait_until, timeout=30000)
                if wait_until == "commit":
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=12000)
                    except Exception:
                        pass
                return response
            except Exception as e:
                last_error = e
                message = str(e)
                retryable = (
                    "ERR_CONNECTION_ABORTED" in message
                    or "Timeout" in message
                    or "Navigation" in message
                )
                if not retryable or attempt == 3:
                    raise
                await page.wait_for_timeout(2000 * attempt)
        if last_error:
            raise last_error
        return None

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
