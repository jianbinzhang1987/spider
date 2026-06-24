"""IC交易网 (ic.net.cn) adapter — Playwright with login session management."""

from __future__ import annotations

import json
import re
import logging
from pathlib import Path
from typing import Any

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.core.browser_pool import BrowserPool
from src.models import PartResult

logger = logging.getLogger(__name__)

# Session state file for persistent login
SESSION_FILE = Path.home() / ".spider_sessions" / "icnet_state.json"


@AdapterRegistry.register("icnet")
class IcnetAdapter(BrowserAdapter):
    """
    IC交易网 adapter.

    Strategy: Playwright with persistent login session.
    - First attempt: load saved session cookies
    - If expired: re-login and save new session
    - Search: standard URL query after auth
    Verified: Search redirects to login page without valid session.
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
            # Try loading saved session first
            if not self._logged_in:
                await self._load_session(page)

            # Attempt search
            url = f"{self.SEARCH_URL}?q={mpn}"
            await page.goto(url, timeout=20000)
            await page.wait_for_timeout(5000)

            # Check if redirected to login
            if "login" in page.url or "member.ic.net.cn" in page.url:
                self._logged_in = False
                login_ok = await self._login(page)
                if not login_ok:
                    return self.failed_result(mpn, "登录失败(需有效账号)")
                # Retry search after login
                await page.goto(url, timeout=20000)
                await page.wait_for_timeout(5000)

            if "login" in page.url:
                return self.failed_result(mpn, "登录态无效")

            content = await page.content()
            return self._parse_results(mpn, content, url)
        except Exception as e:
            logger.error(f"[IC交易网] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    async def _load_session(self, page) -> None:
        """Load previously saved session cookies."""
        if not SESSION_FILE.exists():
            return
        try:
            state = json.loads(SESSION_FILE.read_text())
            cookies = state.get("cookies", [])
            if cookies:
                await page.context.add_cookies(cookies)
                self._logged_in = True
                logger.info("[IC交易网] Loaded saved session")
        except Exception as e:
            logger.warning(f"[IC交易网] Failed to load session: {e}")

    async def _save_session(self, page) -> None:
        """Save session cookies for reuse."""
        try:
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            cookies = await page.context.cookies()
            SESSION_FILE.write_text(json.dumps({"cookies": cookies}, ensure_ascii=False))
            logger.info("[IC交易网] Session saved")
        except Exception as e:
            logger.warning(f"[IC交易网] Failed to save session: {e}")

    async def _login(self, page) -> bool:
        """Perform login flow with multiple form detection strategies."""
        if not self._username or not self._password:
            logger.warning("[IC交易网] No credentials configured")
            return False

        try:
            await page.goto(self.LOGIN_URL, timeout=15000)
            await page.wait_for_timeout(2000)

            # Try multiple username input selectors
            username_selectors = [
                'input[name="username"]',
                'input[name="loginname"]',
                'input[name="user"]',
                'input[placeholder*="用户名"]',
                'input[placeholder*="手机"]',
                'input[placeholder*="账号"]',
                'input[type="text"]:first-of-type',
            ]
            username_input = None
            for sel in username_selectors:
                username_input = await page.query_selector(sel)
                if username_input:
                    break

            password_input = await page.query_selector('input[type="password"]')

            if username_input and password_input:
                await username_input.fill(self._username)
                await password_input.fill(self._password)
                await page.wait_for_timeout(500)

                # Submit
                submit_selectors = [
                    'button[type="submit"]',
                    'input[type="submit"]',
                    '.btn-login',
                    'button:has-text("登录")',
                    'a:has-text("登录")',
                ]
                for sel in submit_selectors:
                    submit_btn = await page.query_selector(sel)
                    if submit_btn:
                        await submit_btn.click()
                        break
                else:
                    await password_input.press("Enter")

                await page.wait_for_timeout(3000)

                if "login" not in page.url:
                    self._logged_in = True
                    await self._save_session(page)
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

        # Try to extract lead time
        lead_time = None
        lt_match = re.search(r'(?:货期|交期|lead)[：:\s]*([^<\n]{2,20})', html, re.I)
        if lt_match:
            lead_time = lt_match.group(1).strip()

        result_data: dict[str, Any] = {
            "mpn": mpn,
            "brand": brand,
            "stock": stock,
            "lead_time": lead_time,
            "product_url": url,
        }

        if price_values:
            result_data["price_unit"] = min(price_values)

        return self.success_result(mpn, result_data)
