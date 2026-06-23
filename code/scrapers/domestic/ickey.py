import time
import logging
from datetime import datetime
from typing import Dict, Any, List
import re
from urllib.parse import quote
from playwright.sync_api import Page, BrowserContext
from scrapers.base_scraper import BaseScraper


class IckeyScraper(BaseScraper):
    def __init__(self, headless: bool = False):
        super().__init__("ickey", headless)

    def login(self, context: BrowserContext, page: Page) -> bool:
        """Logs into 云汉芯城 (Ickey)."""
        self.log(f"Navigating to login page: {self.login_url}")
        page.goto(self.login_url)
        page.wait_for_load_state("networkidle")

        if "login" not in page.url:
            self.log("Already logged in (based on URL).")
            return True

        try:
            pwd_tab = page.locator(
                "text=密码登录, text=账号登录, .login-tab-item:has-text('密码')"
            ).first
            if pwd_tab.is_visible():
                pwd_tab.click()
                time.sleep(0.5)

            username_input = page.locator(
                "input[placeholder*='手机'], input[placeholder*='账号'], "
                "input[placeholder*='用户名']"
            ).first
            password_input = page.locator("input[type='password']").first

            username_input.fill(self.username)
            password_input.fill(self.password)
            time.sleep(0.5)

            submit_btn = page.locator(
                "button:has-text('登录'), .btn-login, input[value='登录']"
            ).first
            submit_btn.click()

            page.wait_for_timeout(3000)

            if page.locator(
                ".geetest_radar_btn, .geetest_slider_button, iframe[src*='geetest']"
            ).first.is_visible() or "login" in page.url:
                self.wait_for_human_intervention(
                    page,
                    "检测到登录校验，请在浏览器中手动完成登录。",
                    "text=退出, .user-name, a[href*='logout']",
                )

            if "login" not in page.url:
                self.log("Login succeeded.")
                self.save_session(context)
                return True
            else:
                if page.locator("text=退出, text=退出登录").first.is_visible():
                    self.log("Found logout button. Login succeeded.")
                    self.save_session(context)
                    return True

        except Exception as e:
            self.log(f"Login error: {e}", logging.ERROR)

        return False

    def search_and_extract(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """Search for the model on Ickey and extract info."""
        encoded_model = quote(model, safe='')
        search_url = f"https://www.ickey.cn/search.html?keyword={encoded_model}"
        self.log(f"Searching for model: {model} using URL: {search_url}")

        self.safe_goto(page, search_url)
        page.wait_for_timeout(3000)

        # If redirected directly to details page
        if "/detail/" in page.url or "/product/" in page.url:
            self.log("Directly redirected to product detail page.")
            return self._extract_details(page, model, quantity)

        # Search results list page
        product_links = page.locator(
            "a[href*='/detail/'], a[href*='/product/']"
        ).all()
        if not product_links:
            product_links = page.locator(
                ".product-name a, .goods-name a, .search-result a[href]"
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
            self.log(f"No exact match text found. Falling back to first result: {target_url}")

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

    def _normalise_url(self, href: str) -> str:
        if not href:
            return ""
        if href.startswith("//"):
            return f"https:{href}"
        if href.startswith("/"):
            return f"https://www.ickey.cn{href}"
        return href

    def _extract_details(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """Extract detailed specifications, stock and prices from Ickey."""
        result = self.get_empty_result(model, quantity)
        result["渠道链接"] = page.url
        result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Fill purchase quantity
        try:
            qty_input = page.locator(
                "#inputNum, input[id*='num'], input[name*='qty'], input[name*='num']"
            ).first
            if qty_input.is_visible():
                qty_input.fill(str(quantity))
                page.keyboard.press("Enter")
                page.wait_for_timeout(1000)
                self.log(f"Filled quantity {quantity} into Ickey input.")
        except Exception as e:
            self.log(f"Failed to fill quantity: {e}", logging.WARNING)

        try:
            # 1. Brand
            brand_val = self._extract_text(page, [
                ".brand-info",
                "td:has-text('品牌') + td",
                ".detail-brand",
                "span:has-text('品牌')",
                "a[href*='brand']",
            ], strip_label="品牌")
            if not brand_val:
                brand_val = self._extract_from_body(page, r'品牌[：:]\s*(\S+)')
            result["品牌"] = brand_val or "未显示"

            # 2. Stock
            stock_val = self._extract_text(page, [
                ".stock-info",
                "td:has-text('库存') + td",
                ".detail-stock",
                "span:has-text('库存')",
            ], strip_label="库存")
            result["库存数量"] = stock_val or "未显示"

            # 3. Lead Time
            lead_val = self._extract_text(page, [
                "td:has-text('货期') + td",
                "span:has-text('货期')",
                "span:has-text('发货时间')",
            ], strip_label="货期")
            result["货期"] = lead_val or "现货"

            # 4. Price Grid
            prices = self._extract_price_tiers(page)

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

    def _extract_text(self, page: Page, selectors: list, strip_label: str = "") -> str:
        for selector in selectors:
            try:
                el = page.locator(selector).first
                if el.is_visible():
                    txt = el.inner_text().strip()
                    if strip_label:
                        for sep in ["：", ":", " "]:
                            key = f"{strip_label}{sep}"
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
        prices: List[tuple] = []

        row_selectors = [
            ".price-list tr",
            ".ladder-price tr",
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

        if not prices:
            qtys = page.locator("[class*='qty'], [class*='num']").all_inner_texts()
            vals = page.locator("[class*='price']").all_inner_texts()
            for q, v in zip(qtys, vals):
                try:
                    q_num = int(re.search(r'\d+', q.replace(",", "")).group())
                    v_num = float(re.search(r'\d+\.\d+', v).group())
                    prices.append((q_num, v_num))
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
        for sel in [".price-num", ".detail-price", ".goods-price", "[class*='price']"]:
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
