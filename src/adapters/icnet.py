"""IC交易网 (ic.net.cn) adapter — Playwright with login session."""

from __future__ import annotations

import re
import logging

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool
from src.models import PartResult

logger = logging.getLogger(__name__)


@AdapterRegistry.register("icnet")
class IcnetAdapter(BrowserAdapter):
    """
    IC交易网 adapter.

    Strategy: Playwright with login session → search after authentication.
    Verified: Search redirects to login page without session.
    Requires: Valid account credentials.
    """

    LOGIN_URL = "https://member.ic.net.cn/login.php"
    SEARCH_URL = "https://www.ic.net.cn/search.php"

    def __init__(self, browser_pool: BrowserPool, username: str = "", password: str = "") -> None:
        super().__init__("IC交易网", browser_pool)
        self._username = username
        self._password = password
        self._logged_in = False

    async def search_by_mpn(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            # Ensure logged in
            if not self._logged_in:
                login_ok = await self._login(page)
                if not login_ok:
                    return self.failed_result(mpn, "登录失败")

            url = f"{self.SEARCH_URL}?q={mpn}"
            await page.goto(url, timeout=20000)
            await page.wait_for_timeout(5000)

            # Check if redirected to login
            if "login" in page.url:
                self._logged_in = False
                return self.failed_result(mpn, "需要登录")

            content = await page.content()
            return self._parse_results(mpn, content, url)
        except Exception as e:
            logger.error(f"[IC交易网] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    async def _login(self, page) -> bool:
        """Perform login flow."""
        if not self._username or not self._password:
            logger.warning("[IC交易网] No credentials configured")
            return False

        try:
            await page.goto(self.LOGIN_URL, timeout=15000)
            await page.wait_for_timeout(2000)

            # Fill credentials
            username_input = await page.query_selector(
                'input[name="username"], input[name="loginname"], input[type="text"]'
            )
            password_input = await page.query_selector('input[type="password"]')

            if username_input and password_input:
                await username_input.fill(self._username)
                await password_input.fill(self._password)
                await page.wait_for_timeout(500)

                # Submit
                submit_btn = await page.query_selector(
                    'button[type="submit"], input[type="submit"], .btn-login'
                )
                if submit_btn:
                    await submit_btn.click()
                else:
                    await password_input.press("Enter")

                await page.wait_for_timeout(3000)

                if "login" not in page.url:
                    self._logged_in = True
                    logger.info("[IC交易网] Login successful")
                    return True

            logger.warning("[IC交易网] Login failed")
            return False
        except Exception as e:
            logger.error(f"[IC交易网] Login error: {e}")
            return False

    def _parse_results(self, mpn: str, html: str, url: str) -> PartResult:
        """Parse product data from search results."""
        mpn_norm = self._normalize_text(mpn)

        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        prices = re.findall(r'[￥¥$]\s*(\d+\.?\d*)', html)
        price_values = [float(p) for p in prices if 0.0001 < float(p) < 100000]

        stock = None
        stock_match = re.search(r'(?:库存|stock|数量)[：:\s]*(\d[\d,]*)', html, re.I)
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
