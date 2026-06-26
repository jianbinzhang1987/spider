"""ICGOO (icgoo.net) adapter — Playwright with API response interception."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.adapters.base import BrowserAdapter
from src.adapters.registry import AdapterRegistry
from src.config import get
from src.core.browser_pool import BrowserPool
from src.models import PartResult, PriceBreak

logger = logging.getLogger(__name__)


@AdapterRegistry.register("icgoo")
class IcgooAdapter(BrowserAdapter):
    """
    ICGOO adapter.

    Strategy: Playwright renders Vue SPA → intercept API responses from v8back.icgoo.net.
    Known APIs (browser-session only):
      - /api/search/suggestions/?q={keyword} → matching part numbers
      - /api/search/supplier/{partno}/1/ → supplier/price data
      - /api/search/batch_price/ → batch pricing
    Note: These APIs return HTML (SPA shell) when called directly via curl_cffi,
          but return JSON when called within a browser session.
    """

    LOGIN_URL = "https://www.icgoo.net/login.html"

    def __init__(self, browser_pool: BrowserPool) -> None:
        super().__init__("ICGOO", browser_pool)

    async def search_by_mpn(self, mpn: str) -> PartResult:
        page = await self._new_page()
        try:
            api_data: dict[str, Any] = {}
            blocked_resources: list[str] = []

            async def capture_response(response):
                url = response.url
                if response.status in (401, 403) and "media.icgoo.net" in url:
                    blocked_resources.append(url)
                # Capture any API response from icgoo backend
                if "icgoo.net/api/" in url or "v8back.icgoo.net" in url:
                    try:
                        ct = response.headers.get("content-type", "")
                        if "json" not in ct and "text" not in ct:
                            return
                        text = await response.text()
                        if not text or (not text.startswith("{") and not text.startswith("[")):
                            return
                        data = json.loads(text)
                        if "supplier" in url or "search" in url:
                            api_data["supplier"] = data
                        if "price" in url or "batch" in url:
                            api_data["price"] = data
                        # Store all API responses for fallback
                        if "all" not in api_data:
                            api_data["all"] = []
                        api_data["all"].append(data)
                    except Exception:
                        pass

            page.on("response", capture_response)
            url = f"https://www.icgoo.net/search/{mpn}/1"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(12000)

            if await self._page_needs_login_or_reload(page, mpn, blocked_resources):
                logged_in = await self._ensure_login(page)
                if not logged_in:
                    return self.failed_result(
                        mpn,
                        "ICGOO关键静态资源加载失败或登录组件未加载，无法获取搜索结果；请用可见浏览器完成登录后重试",
                    )
                api_data.clear()
                blocked_resources.clear()
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(12000)

            # Try API-intercepted data first
            if api_data.get("supplier"):
                result = self._parse_supplier_api(mpn, api_data["supplier"], url)
                if result.status.value == "success":
                    # If no price from supplier API, try batch_price data
                    if result.price_unit is None and api_data.get("price"):
                        price = self._extract_batch_price(mpn, api_data["price"])
                        if price is not None:
                            result.price_unit = price
                    # If still no price, try all captured API responses
                    if result.price_unit is None and api_data.get("all"):
                        for resp_data in api_data["all"]:
                            price = self._extract_batch_price(mpn, resp_data)
                            if price is not None:
                                result.price_unit = price
                                break
                    # If still no price, try DOM extraction
                    if result.price_unit is None:
                        content = await page.content()
                        dom_price = self._extract_price_from_dom(content)
                        if dom_price is not None:
                            result.price_unit = dom_price
                    return result

            # Fallback: parse rendered DOM (try clicking into first product if available)
            content = await page.content()
            result = self._parse_dom(mpn, content, url)

            # If found but no price, try navigating to product detail page
            if result.status.value == "success" and result.price_unit is None:
                detail_url = await self._find_product_link(page, mpn)
                if detail_url:
                    await page.goto(detail_url, timeout=20000)
                    await page.wait_for_timeout(8000)
                    detail_content = await page.content()
                    detail_price = self._extract_price_from_dom(detail_content)
                    if detail_price is not None:
                        result.price_unit = detail_price
                        result.product_url = detail_url
                if result.price_unit is None:
                    return self.failed_result(
                        mpn,
                        "ICGOO返回了匹配型号但未返回可解析价格，可能需要登录权限、询价或关键资源未完整加载",
                    )

            return result
        except Exception as e:
            logger.error(f"[ICGOO] search failed: {e}")
            return self.failed_result(mpn, str(e))
        finally:
            await self._release_page(page)

    async def _page_needs_login_or_reload(
        self,
        page,
        mpn: str,
        blocked_resources: list[str],
    ) -> bool:
        """Detect the empty shell caused by blocked static assets or missing session."""
        try:
            content = await page.content()
            body_text = await page.locator("body").inner_text(timeout=3000)
        except Exception:
            return True
        if blocked_resources:
            logger.warning("[ICGOO] static resources blocked: %s", len(blocked_resources))
            return True
        if self._normalize_text(mpn) in self._normalize_text(content):
            return False
        footer_only_markers = ["粤公网安备", "粤ICP备", "营业执照"]
        if all(marker in body_text for marker in footer_only_markers) and len(body_text) < 300:
            return True
        return False

    async def _ensure_login(self, page) -> bool:
        """Login to ICGOO using configured credentials or wait for manual login."""
        username = get("icgoo.username")
        password = get("icgoo.password")

        try:
            await page.goto(self.LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(8000)
        except Exception:
            return False

        if await self._is_logged_in(page):
            return True

        visible_inputs = page.locator("input:visible")
        input_count = await visible_inputs.count()
        if input_count >= 2 and username and password:
            try:
                user_input = visible_inputs.nth(0)
                pass_input = page.locator(
                    'input[type="password"]:visible, input[placeholder*="密码"]:visible'
                ).first
                await user_input.fill(username)
                await pass_input.fill(password)
                submit = page.locator(
                    'button:has-text("登录"), a:has-text("登录"), button[type="submit"]'
                ).first
                await submit.click()
                await page.wait_for_timeout(8000)
                if await self._is_logged_in(page):
                    return True
            except Exception as e:
                logger.warning("[ICGOO] configured login failed: %s", e)

        if self._pool.headless:
            return False

        logger.warning("[ICGOO] 请在弹出的浏览器中手动登录，程序会自动继续。")
        try:
            await page.bring_to_front()
        except Exception:
            pass

        import asyncio
        deadline = asyncio.get_running_loop().time() + 180
        while asyncio.get_running_loop().time() < deadline:
            await page.wait_for_timeout(3000)
            if await self._is_logged_in(page):
                return True
        return False

    async def _is_logged_in(self, page) -> bool:
        try:
            text = await page.locator("body").inner_text(timeout=3000)
        except Exception:
            return False
        login_markers = ["退出", "会员中心", "个人中心", "我的订单", "购物车"]
        return any(marker in text for marker in login_markers)

    async def _find_product_link(self, page, mpn: str) -> str | None:
        """Try to find a product detail link on the search results page."""
        try:
            content = await page.content()
            # ICGOO product detail links: /part/xxxxx.html
            links = re.findall(r'href="(/part/[^"]+)"', content)
            if links:
                return f"https://www.icgoo.net{links[0]}"
            # Also try clicking on a product element
            product_el = await page.query_selector('a[href*="/part/"]')
            if product_el:
                href = await product_el.get_attribute("href")
                if href:
                    if href.startswith("/"):
                        return f"https://www.icgoo.net{href}"
                    return href
        except Exception:
            pass
        return None

    def _extract_price_from_dom(self, html: str) -> float | None:
        """Extract price from any page's DOM content."""
        price_values: list[float] = []

        # Pattern 1: Currency symbol followed by number
        prices = re.findall(r'[￥¥$]\s*(\d+\.?\d*)', html)
        price_values.extend(float(p) for p in prices if 0.0001 < float(p) < 100000)

        # Pattern 2: Price in data attributes or JSON embedded in page
        data_prices = re.findall(r'"(?:price|unitPrice|unit_price|sell_price|cny_price|min_price)"[:\s]*["\']?(\d+\.?\d*)', html)
        price_values.extend(float(p) for p in data_prices if 0.0001 < float(p) < 100000)

        # Pattern 3: Ladder price patterns (e.g., "1+ ¥0.05")
        ladder_prices = re.findall(r'\d+\+\s*[￥¥$]?\s*(\d+\.?\d+)', html)
        price_values.extend(float(p) for p in ladder_prices if 0.0001 < float(p) < 100000)

        # Pattern 4: class="price" or similar
        price_spans = re.findall(r'class="[^"]*price[^"]*"[^>]*>([^<]+)', html, re.I)
        for text in price_spans:
            nums = re.findall(r'(\d+\.?\d+)', text)
            for n in nums:
                v = float(n)
                if 0.0001 < v < 100000:
                    price_values.append(v)

        # Pattern 5: td/span with price-like content near "单价" or "价格"
        price_near_label = re.findall(r'(?:单价|价格|报价)[^<]{0,50}?(\d+\.?\d+)', html)
        price_values.extend(float(p) for p in price_near_label if 0.0001 < float(p) < 100000)

        return min(price_values) if price_values else None

    def _parse_supplier_api(self, mpn: str, data: dict, url: str) -> PartResult:
        """Parse the supplier API JSON response."""
        try:
            # The API returns supplier offers with pricing
            items = None
            if isinstance(data, dict):
                items = (
                    data.get("data") or data.get("items") or data.get("list")
                    or data.get("results") or data.get("products")
                )
                # Handle nested: {data: {list: [...]}}
                if isinstance(items, dict):
                    items = items.get("list") or items.get("items") or items.get("data")
            if isinstance(data, list):
                items = data

            if not items or not isinstance(items, list):
                return self.not_found_result(mpn)

            # Get the first/best offer
            best = items[0] if items else None
            if not best or not isinstance(best, dict):
                return self.not_found_result(mpn)

            # Price extraction — try multiple field names
            price = (
                best.get("price") or best.get("unit_price") or best.get("unitPrice")
                or best.get("min_price") or best.get("minPrice")
                or best.get("cny_price") or best.get("sell_price")
                or best.get("ladder_price")
            )
            # Try nested price_breaks/ladder
            price_breaks = []
            ladder = best.get("price_breaks") or best.get("ladder") or best.get("prices") or []
            if isinstance(ladder, list):
                for item in ladder:
                    if isinstance(item, dict):
                        qty = item.get("qty") or item.get("quantity") or item.get("num")
                        p = item.get("price") or item.get("unit_price") or item.get("cny_price")
                        if qty and p:
                            price_breaks.append({"quantity": qty, "unit_price": p})
                if not price and price_breaks:
                    price = price_breaks[0]["unit_price"]

            return self.success_result(mpn, {
                "mpn": best.get("partno") or best.get("mpn") or best.get("goods_name") or mpn,
                "brand": (
                    best.get("mfr") or best.get("brand") or best.get("manufacturer")
                    or best.get("brand_name")
                ),
                "stock": (
                    best.get("stock") or best.get("inventory")
                    or best.get("qty") or best.get("available")
                ),
                "price_unit": price,
                "price_breaks": price_breaks,
                "moq": best.get("moq") or best.get("min_qty") or best.get("mpq"),
                "product_url": url,
                "description": best.get("desc") or best.get("description"),
            })
        except Exception as e:
            logger.warning(f"[ICGOO] API parse error: {e}")
            return self.not_found_result(mpn)

    def _extract_batch_price(self, mpn: str, data: Any) -> float | None:
        """Extract price from batch_price API response."""
        try:
            items = data if isinstance(data, list) else (
                data.get("data") or data.get("items") or data.get("list") or []
            )
            if isinstance(items, dict):
                items = items.get("data") or items.get("list") or []
            if not isinstance(items, list):
                return None

            mpn_norm = self._normalize_text(mpn)
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_mpn = item.get("partno") or item.get("mpn") or item.get("goods_name") or ""
                if mpn_norm not in self._normalize_text(str(item_mpn)):
                    continue
                # Try all price fields
                price = (
                    item.get("price") or item.get("unit_price") or item.get("cny_price")
                    or item.get("min_price") or item.get("sell_price")
                )
                if price:
                    try:
                        return float(price)
                    except (ValueError, TypeError):
                        pass
                # Try ladder
                ladder = item.get("ladder") or item.get("prices") or item.get("price_breaks") or []
                if isinstance(ladder, list) and ladder:
                    for lb in ladder:
                        if isinstance(lb, dict):
                            p = lb.get("price") or lb.get("unit_price") or lb.get("cny_price")
                            if p:
                                try:
                                    return float(p)
                                except (ValueError, TypeError):
                                    pass
        except Exception:
            pass
        return None

    def _parse_dom(self, mpn: str, html: str, url: str) -> PartResult:
        """Fallback: parse rendered DOM for product info."""
        mpn_norm = self._normalize_text(mpn)
        if mpn_norm not in self._normalize_text(html):
            return self.not_found_result(mpn)

        # ICGOO shows inquiry button (询价) rather than direct prices for many items
        has_inquiry = "询价" in html

        # Extract price from DOM
        price = self._extract_price_from_dom(html)

        result_data: dict[str, Any] = {
            "mpn": mpn,
            "product_url": url,
        }

        if price is not None:
            result_data["price_unit"] = price

        if has_inquiry and price is None:
            result_data["description"] = "需询价"

        return self.success_result(mpn, result_data)
