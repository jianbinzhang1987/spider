import time
import logging
from datetime import datetime
from typing import Dict, Any, List
import re
from urllib.parse import quote
from playwright.sync_api import Page, BrowserContext
from scrapers.base_scraper import BaseScraper

class SzlcscScraper(BaseScraper):
    def __init__(self, headless: bool = False):
        super().__init__("szlcsc", headless)

    def login(self, context: BrowserContext, page: Page) -> bool:
        """Logs into 立创商城 (SZLCSC)."""
        self.log(f"Navigating to login page: {self.login_url}")
        page.goto(self.login_url)
        page.wait_for_load_state("networkidle")

        if "login" not in page.url:
            self.log("Already logged in (based on URL redirect).")
            return True

        try:
            password_login_tab = page.locator("text=密码登录").first
            if password_login_tab.is_visible():
                password_login_tab.click()
                time.sleep(0.5)

            username_input = page.locator(
                "input[placeholder*='手机'], input[placeholder*='账号'], "
                "input[placeholder*='邮箱'], input[placeholder*='用户名']"
            ).first
            password_input = page.locator("input[type='password']").first

            username_input.fill(self.username)
            password_input.fill(self.password)
            time.sleep(0.5)

            submit_btn = page.locator(
                "button:has-text('登录'), input[type='submit'], .btn-login, "
                "button:has-text('登 录')"
            ).first
            submit_btn.click()

            page.wait_for_timeout(3000)

            if page.locator(
                ".geetest_radar_btn, .geetest_slider_button, iframe[src*='geetest']"
            ).first.is_visible() or "login" in page.url:
                self.wait_for_human_intervention(
                    page,
                    "检测到登录验证码/滑块，请在弹出的浏览器中手动完成登录。",
                    "a:has-text('退出'), .user-name, div[class*='user']",
                )

            if "login.html" not in page.url:
                self.log("Login succeeded.")
                self.save_session(context)
                return True
            else:
                self.log("Login page still active, checking user indicator...")
                if page.locator("text=退出, text=个人中心").first.is_visible():
                    self.log("User indicators found. Login succeeded.")
                    self.save_session(context)
                    return True

        except Exception as e:
            self.log(f"Login error: {e}", logging.ERROR)

        return False

    def search_and_extract(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """Search for the model on SZLCSC and extract information."""
        encoded_model = quote(model, safe='')
        search_url = f"https://so.szlcsc.com/global.html?k={encoded_model}"
        self.log(f"Searching for model: {model} using URL: {search_url}")

        self.safe_goto(page, search_url)
        page.wait_for_timeout(3000)

        # 1. Check if redirected to detail page
        if "item.szlcsc.com" in page.url or "/product/details_" in page.url:
            self.log("Directly redirected to product detail page.")
            return self._extract_details(page, model, quantity)

        # 2. Search results page – find matching product link
        link_selectors = [
            "a[href*='item.szlcsc.com']",
            "a[href*='/product/details_']",
            ".product-list a[href]",
            ".search-result a[href]",
            "table a[href*='szlcsc']",
        ]
        product_links = []
        for sel in link_selectors:
            product_links = page.locator(sel).all()
            if product_links:
                break

        if not product_links:
            product_links = page.locator(".product-title a, .product-item a").all()

        target_url = None
        clean_model = re.sub(r'\s+', '', model).lower()

        for link in product_links:
            href = link.get_attribute("href") or ""
            text = link.inner_text().strip()
            clean_text = re.sub(r'\s+', '', text).lower()

            if clean_model == clean_text or clean_model in clean_text:
                target_url = self._normalise_url(href, "https://so.szlcsc.com")
                break

        # Fallback: first result
        if not target_url and product_links:
            href = product_links[0].get_attribute("href") or ""
            target_url = self._normalise_url(href, "https://so.szlcsc.com")
            self.log(f"No exact match found. Falling back to first result: {target_url}")

        if not target_url:
            self.log(f"Model {model} not found in search results.")
            result = self.get_empty_result(model, quantity)
            result["渠道链接"] = page.url
            result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return result

        self.log(f"Navigating to detail page: {target_url}")
        self.safe_goto(page, target_url)
        page.wait_for_timeout(2000)

        return self._extract_details(page, model, quantity)

    def _normalise_url(self, href: str, default_host: str) -> str:
        if not href:
            return ""
        if href.startswith("//"):
            return f"https:{href}"
        if href.startswith("/"):
            return f"{default_host}{href}"
        if href.startswith("http"):
            return href
        return f"{default_host}/{href}"

    def _extract_details(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """Extract details from the product detail page."""
        result = self.get_empty_result(model, quantity)
        result["渠道链接"] = page.url
        result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            # 1. Extract Brand
            brand_val = self._extract_text_by_label(page, [
                "td:has-text('品牌') + td",
                ".brand-name",
                "a[href*='brand']",
                "span:has-text('品牌')",
            ], label_strip="品牌")
            if not brand_val:
                brand_val = self._extract_from_body(page, r'品牌[：:]\s*(\S+)')
            result["品牌"] = brand_val or "未显示"

            # 2. Extract Stock
            stock_val = self._extract_text_by_label(page, [
                ".stock-num",
                "td:has-text('库存') + td",
                "span:has-text('库存')",
                "text=现货",
            ], label_strip="库存")
            if not stock_val:
                stock_val = self._extract_from_body(page, r'库存[：:]\s*([\d,]+)')
            result["库存数量"] = stock_val or "未显示"

            # 3. Lead time
            lead_val = self._extract_text_by_label(page, [
                "text=发货时间",
                "text=货期",
                "td:has-text('货期') + td",
            ], label_strip="货期")
            result["货期"] = lead_val or "现货"

            # 4. Extract Price Grid
            prices = self._extract_price_tiers(page)

            # 5. Pick applicable price
            if prices:
                applicable_price, note = self._pick_tier_price(prices, quantity)
                cny_price, orig_price_str = self.convert_price_to_cny(str(applicable_price), "CNY")
                result["适用价格(人民币)"] = cny_price
                result["原始币种价格"] = f"{orig_price_str} {note}".strip()
            else:
                single_price = self._extract_single_price(page)
                if single_price is not None:
                    cny_price, orig_price_str = self.convert_price_to_cny(str(single_price), "CNY")
                    result["适用价格(人民币)"] = cny_price
                    result["原始币种价格"] = orig_price_str
                else:
                    result["适用价格(人民币)"] = None
                    result["原始币种价格"] = "未显示价格"
                    result["货期"] = "无价格信息"

        except Exception as e:
            self.log(f"Error parsing details: {e}", logging.ERROR)
            result["货期"] = f"解析失败: {str(e)[:40]}"

        return result

    # ─── helpers ───

    def _extract_text_by_label(self, page: Page, selectors: list, label_strip: str = "") -> str:
        for selector in selectors:
            try:
                el = page.locator(selector).first
                if el.is_visible():
                    txt = el.inner_text().strip()
                    if label_strip:
                        for sep in ["：", ":", " "]:
                            key = f"{label_strip}{sep}"
                            if key in txt:
                                txt = txt.split(key, 1)[-1].strip()
                    if txt:
                        return txt
            except Exception:
                pass
        return ""

    def _extract_from_body(self, page: Page, pattern: str) -> str:
        try:
            body_text = page.locator("body").inner_text()
            m = re.search(pattern, body_text)
            if m:
                return m.group(1).strip()
        except Exception:
            pass
        return ""

    def _extract_price_tiers(self, page: Page) -> List[tuple]:
        """Try multiple strategies to extract (min_qty, unit_price) tuples."""
        prices: List[tuple] = []

        # Strategy 1: table rows with explicit price-list classes
        row_selectors = [
            ".price-list tr",
            "table.price-table tr",
            ".ladder-price-item",
            ".price-range-item",
            ".price-break tr",
            "table tr",
        ]
        for sel in row_selectors:
            rows = page.locator(sel).all()
            for row in rows:
                text = re.sub(r'\s+', ' ', row.inner_text().strip())
                matches = re.findall(r'(\d[\d,]*)\+?\s*[~\-]?\s*(?:\d[\d,]*)?\s*[¥￥$]?\s*(\d+\.\d+)', text)
                for qty_str, price_str in matches:
                    try:
                        qty = int(qty_str.replace(",", ""))
                        price_val = float(price_str)
                        prices.append((qty, price_val))
                    except Exception:
                        pass
            if prices:
                break

        # Strategy 2: separate qty / price DOM elements
        if not prices:
            qty_sels = ".price-list .qty, .ladder-price .qty, td[class*='qty'], [class*='range']"
            price_sels = ".price-list .price, .ladder-price .price, td[class*='price'], [class*='unit-price']"
            qtys = page.locator(qty_sels).all_inner_texts()
            vals = page.locator(price_sels).all_inner_texts()
            for q, v in zip(qtys, vals):
                try:
                    q_num = int(re.search(r'\d+', q.replace(",", "")).group())
                    v_num = float(re.search(r'\d+\.\d+', v).group())
                    prices.append((q_num, v_num))
                except Exception:
                    pass

        # Strategy 3: body text line-pair matching
        if not prices:
            try:
                body_text = page.locator("body").inner_text()
                lines = [l.strip() for l in body_text.split("\n") if l.strip()]
                for i in range(len(lines) - 1):
                    line = lines[i]
                    next_line = lines[i + 1]
                    qty_match = re.match(r'^([\d,]+)\+?$', line)
                    price_match = re.match(r'^[￥¥$]?\s*(\d+\.\d+)$', next_line)
                    if qty_match and price_match:
                        qty = int(qty_match.group(1).replace(",", ""))
                        val = float(price_match.group(1))
                        prices.append((qty, val))
            except Exception:
                pass

        prices = sorted(list(set(prices)), key=lambda x: x[0])
        self.log(f"Extracted price tiers: {prices}")
        return prices

    def _pick_tier_price(self, prices: List[tuple], quantity: int) -> tuple:
        applicable_price = None
        note = ""
        for tier_qty, tier_price in reversed(prices):
            if quantity >= tier_qty:
                applicable_price = tier_price
                break
        if applicable_price is None:
            applicable_price = prices[0][1]
            note = f"(最小起购量为 {prices[0][0]}, 取此档价格)"
        return applicable_price, note

    def _extract_single_price(self, page: Page) -> float:
        for sel in [".price-num", ".price", ".product-price", "[class*='price']"]:
            try:
                el = page.locator(sel).first
                if el.is_visible():
                    txt = el.inner_text().strip()
                    m = re.search(r'(\d+\.\d+)', txt)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass
        return None
