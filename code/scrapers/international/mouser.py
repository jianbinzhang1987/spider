import time
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import re
import json
import httpx
from urllib.parse import quote
from playwright.sync_api import Page, BrowserContext
from scrapers.base_scraper import BaseScraper
from config import CREDENTIALS


# Mouser Search API v2 endpoint
MOUSER_API_URL = "https://api.mouser.com/api/v2/search/keyword"

# API Key – set this in config or environment variable MOUSER_API_KEY
MOUSER_API_KEY = CREDENTIALS.get("mouser", {}).get("api_key", "")


class MouserScraper(BaseScraper):
    """
    Mouser scraper using the official Search API (preferred) with browser
    fallback.  The API avoids all anti-automation blocks.
    """

    def __init__(self, headless: bool = False):
        super().__init__("mouser", headless)

    # ───────── API path ─────────

    def _search_via_api(self, model: str, quantity: int) -> Optional[Dict[str, Any]]:
        """Call the Mouser Search API and return a result dict, or None on failure."""
        api_key = MOUSER_API_KEY
        if not api_key:
            self.log("No Mouser API key configured – skipping API path.", logging.INFO)
            return None

        payload = {
            "SearchByKeywordRequest": {
                "keyword": model,
                "records": 10,
                "startingRecord": 0,
                "searchOptions": "1",   # 1 = exact match preferred
                "searchWithYourSignUpLanguage": "zh-CN",
            }
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        url = f"{MOUSER_API_URL}?apiKey={api_key}"

        self.log(f"Calling Mouser API for: {model}")
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=15.0)
            if resp.status_code != 200:
                self.log(f"Mouser API returned HTTP {resp.status_code}", logging.WARNING)
                return None

            data = resp.json()
            parts = data.get("SearchResults", {}).get("Parts", [])
            if not parts:
                self.log(f"Mouser API: no parts found for {model}")
                return None

            # Pick the best match
            clean_model = re.sub(r'\s+', '', model).lower()
            best = None
            for p in parts:
                mpn = re.sub(r'\s+', '', p.get("ManufacturerPartNumber", "")).lower()
                if mpn == clean_model:
                    best = p
                    break
            if best is None:
                best = parts[0]

            return self._api_part_to_result(best, model, quantity)

        except Exception as e:
            self.log(f"Mouser API call failed: {e}", logging.WARNING)
            return None

    def _api_part_to_result(self, part: dict, model: str, quantity: int) -> Dict[str, Any]:
        result = self.get_empty_result(model, quantity)
        result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        result["品牌"] = part.get("Manufacturer", "未显示")
        result["渠道链接"] = part.get("ProductDetailUrl", "")

        # Stock
        avail = part.get("Availability", "")
        stock_match = re.search(r'(\d[\d,]*)', avail.replace(" ", ""))
        result["库存数量"] = stock_match.group(1) if stock_match else avail

        # Lead time
        result["货期"] = part.get("LeadTime", "现货") or "现货"

        # Price tiers
        price_breaks = part.get("PriceBreaks", [])
        prices: List[tuple] = []
        currency = "USD"
        for pb in price_breaks:
            try:
                qty = int(pb.get("Quantity", 0))
                raw_price = pb.get("Price", "0")
                # Price may be "$1.23" or "¥8.50"
                if "¥" in raw_price or "￥" in raw_price:
                    currency = "CNY"
                price_num = float(re.sub(r'[^\d.]', '', raw_price))
                prices.append((qty, price_num))
            except Exception:
                pass

        if prices:
            applicable_price, note = self._pick_tier_price(prices, quantity)
            cny_price, orig = self.convert_price_to_cny(str(applicable_price), currency)
            result["适用价格(人民币)"] = cny_price
            result["原始币种价格"] = f"{orig} {note}".strip()
        else:
            result["适用价格(人民币)"] = None
            result["原始币种价格"] = "未显示价格"
            result["货期"] = "无价格信息"

        return result

    def _pick_tier_price(self, prices: List[tuple], quantity: int) -> tuple:
        applicable = None
        note = ""
        for qty, price in reversed(sorted(prices)):
            if quantity >= qty:
                applicable = price
                break
        if applicable is None:
            applicable = prices[0][1]
            note = f"(最小起购量为 {prices[0][0]}, 取此档价格)"
        return applicable, note

    # ───────── Browser fallback ─────────

    def login(self, context: BrowserContext, page: Page) -> bool:
        """Login to Mouser (browser path only)."""
        self.log(f"Navigating to login page: {self.login_url}")
        try:
            page.goto(self.login_url, timeout=30000)
            page.wait_for_load_state("networkidle")
        except Exception as e:
            self.log(f"Login page load failed: {e}", logging.WARNING)
            return False

        if "login" not in page.url.lower():
            self.log("Already logged in.")
            return True

        try:
            username_input = page.locator(
                "input[type='email'], input[id*='email'], input[name*='email'], "
                "input[name*='user']"
            ).first
            password_input = page.locator("input[type='password']").first

            username_input.fill(self.username)
            password_input.fill(self.password)
            time.sleep(0.5)

            submit_btn = page.locator(
                "button[type='submit'], button:has-text('登录'), "
                "button:has-text('Sign In'), button:has-text('Log In')"
            ).first
            submit_btn.click()
            page.wait_for_timeout(4000)

            if "login" not in page.url.lower():
                self.log("Login succeeded.")
                self.save_session(context)
                return True

        except Exception as e:
            self.log(f"Login error: {e}", logging.ERROR)

        return False

    def search_and_extract(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """Search Mouser – try API first, then fall back to browser scraping."""
        # API path (no browser needed, avoids all anti-bot)
        api_result = self._search_via_api(model, quantity)
        if api_result is not None:
            return api_result

        # Browser fallback
        self.log("Falling back to browser-based Mouser search.")
        return self._search_browser(page, model, quantity)

    def _search_browser(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """Browser-based search fallback."""
        encoded_model = quote(model, safe='')
        search_url = f"https://www.mouser.cn/c/?q={encoded_model}"
        self.log(f"Browser search URL: {search_url}")

        try:
            self.safe_goto(page, search_url)
        except Exception as nav_err:
            self.log(f"Navigation failed: {nav_err}", logging.WARNING)
            result = self.get_empty_result(model, quantity)
            result["渠道链接"] = search_url
            result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            result["货期"] = f"页面加载失败: {str(nav_err)[:40]}"
            return result

        page.wait_for_timeout(3000)

        # Detect access block
        try:
            body_text = page.locator("body").inner_text()
            block_phrases = [
                "访问暂时受限", "Access Denied", "由于我们认为您是在使用自动化工具",
            ]
            if any(p in body_text for p in block_phrases):
                self.log("Mouser blocked automation. Returning graceful error.", logging.WARNING)
                result = self.get_empty_result(model, quantity)
                result["渠道链接"] = page.url
                result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                result["货期"] = "访问受限(建议配置API Key)"
                return result
        except Exception:
            pass

        # Check if directly on detail page
        if "/ProductDetail/" in page.url or "/productdetail/" in page.url.lower():
            return self._extract_details(page, model, quantity)

        # Search results
        product_links = page.locator(
            "a[href*='/ProductDetail/'], a[href*='/productdetail/']"
        ).all()
        if not product_links:
            product_links = page.locator(
                ".search-result a[href], table a[href*='mouser']"
            ).all()

        target_url = None
        clean_model = re.sub(r'\s+', '', model).lower()

        for link in product_links:
            href = link.get_attribute("href") or ""
            text = link.inner_text().strip()
            clean_text = re.sub(r'\s+', '', text).lower()
            if clean_model == clean_text or clean_model in clean_text:
                target_url = self._normalise_url(href)
                break

        if not target_url and product_links:
            href = product_links[0].get_attribute("href") or ""
            target_url = self._normalise_url(href)

        if not target_url:
            result = self.get_empty_result(model, quantity)
            result["渠道链接"] = page.url
            result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            result["货期"] = "未找到型号"
            return result

        try:
            self.safe_goto(page, target_url)
        except Exception:
            result = self.get_empty_result(model, quantity)
            result["渠道链接"] = target_url
            result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            result["货期"] = "详情页加载失败"
            return result

        page.wait_for_timeout(2000)
        return self._extract_details(page, model, quantity)

    def _normalise_url(self, href: str) -> str:
        if not href:
            return ""
        if href.startswith("//"):
            return f"https:{href}"
        if href.startswith("/"):
            return f"https://www.mouser.cn{href}"
        return href

    def _extract_details(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        result = self.get_empty_result(model, quantity)
        result["渠道链接"] = page.url
        result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            brand_val = self._extract_text(page, [
                "[data-testid='manufacturer-name']",
                "a[href*='/manufacturer/']",
                "span:has-text('制造商')",
            ])
            result["品牌"] = brand_val or "未显示"

            stock_val = self._extract_text(page, [
                "[data-testid='lnkInStock']",
                "span:has-text('有货')",
                "span:has-text('In Stock')",
            ])
            result["库存数量"] = stock_val or "未显示"
            result["货期"] = "现货"

            prices, currency = self._extract_price_tiers_browser(page)
            if prices:
                applicable, note = self._pick_tier_price(prices, quantity)
                cny, orig = self.convert_price_to_cny(str(applicable), currency)
                result["适用价格(人民币)"] = cny
                result["原始币种价格"] = f"{orig} {note}".strip()
            else:
                result["适用价格(人民币)"] = None
                result["原始币种价格"] = "未显示价格"
                result["货期"] = "无价格信息"

        except Exception as e:
            self.log(f"Detail parse error: {e}", logging.ERROR)
            result["货期"] = f"解析失败: {str(e)[:40]}"

        return result

    def _extract_text(self, page: Page, selectors: list) -> str:
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible():
                    return el.inner_text().strip()
            except Exception:
                pass
        return ""

    def _extract_price_tiers_browser(self, page: Page) -> tuple:
        prices: List[tuple] = []
        currency = "USD"
        for sel in [".pricing-table tr", "table[class*='price'] tr", "table tr"]:
            rows = page.locator(sel).all()
            for row in rows:
                text = re.sub(r'\s+', ' ', row.inner_text().strip())
                if "¥" in text or "CNY" in text:
                    currency = "CNY"
                matches = re.findall(r'(\d[\d,]*)\+?\s*[~\-]?\s*(?:\d[\d,]*)?\s*[¥￥$]?\s*(\d+\.\d+)', text)
                for qs, ps in matches:
                    try:
                        prices.append((int(qs.replace(",", "")), float(ps)))
                    except Exception:
                        pass
            if prices:
                break
        return sorted(set(prices)), currency
