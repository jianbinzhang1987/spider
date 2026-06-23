import time
import logging
from datetime import datetime
from typing import Dict, Any, List
import re
from urllib.parse import quote
from playwright.sync_api import Page, BrowserContext
from scrapers.base_scraper import BaseScraper


class HqewScraper(BaseScraper):
    def __init__(self, headless: bool = False):
        super().__init__("hqew", headless)

    def login(self, context: BrowserContext, page: Page) -> bool:
        """Logs into 华强电子网 (HQEW)."""
        self.log(f"Navigating to login page: {self.login_url}")
        page.goto(self.login_url)
        page.wait_for_load_state("networkidle")

        if "login" not in page.url:
            self.log("Already logged in (based on URL redirect).")
            return True

        try:
            pwd_tab = page.locator("text=密码登录, text=账号登录, .login-type-pwd").first
            if pwd_tab.is_visible():
                pwd_tab.click()
                time.sleep(0.5)

            username_input = page.locator(
                "input[placeholder*='用户名'], input[placeholder*='手机'], input[placeholder*='账号']"
            ).first
            password_input = page.locator("input[type='password']").first

            username_input.fill(self.username)
            password_input.fill(self.password)
            time.sleep(0.5)

            submit_btn = page.locator(
                "button:has-text('登录'), .btn-login, input[type='button']"
            ).first
            submit_btn.click()

            page.wait_for_timeout(3000)

            if page.locator(
                ".geetest_radar_btn, .geetest_slider_button, iframe[src*='geetest']"
            ).first.is_visible() or "login" in page.url:
                self.wait_for_human_intervention(
                    page,
                    "检测到登录验证，请在浏览器中手动完成登录。",
                    "text=退出, .user-center, a[href*='logout']",
                )

            if "login" not in page.url:
                self.log("Login succeeded.")
                self.save_session(context)
                return True
            else:
                if page.locator("text=退出, text=退出登录").first.is_visible():
                    self.log("Found logout indicators. Login succeeded.")
                    self.save_session(context)
                    return True

        except Exception as e:
            self.log(f"Login error: {e}", logging.ERROR)

        return False

    def search_and_extract(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """
        Search for the model on HQEW using the search box on the homepage.
        HQEW requires interacting with the search input + clicking the search
        button rather than navigating to a constructed URL directly.
        """
        result = self.get_empty_result(model, quantity)
        result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Step 1: Navigate to hqew.com homepage (IC/元器件 search page)
        home_url = "https://www.hqew.com"
        self.log(f"Navigating to HQEW homepage: {home_url}")
        try:
            self.safe_goto(page, home_url)
        except Exception as e:
            self.log(f"Failed to load HQEW homepage: {e}", logging.WARNING)
            result["渠道链接"] = home_url
            result["货期"] = f"首页加载失败: {str(e)[:40]}"
            return result

        page.wait_for_timeout(2000)

        # Step 2: Find the search input box and type the model number
        search_input = None
        search_selectors = [
            "input#keyword",
            "input[name='keyword']",
            "input[placeholder*='型号']",
            "input[placeholder*='搜索']",
            "input[placeholder*='关键词']",
            "input.search-input",
            ".search-box input[type='text']",
            "input[id*='search']",
            "input[name*='search']",
            "#headerSearchInput",
        ]
        for sel in search_selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible():
                    search_input = el
                    self.log(f"Found search input with selector: {sel}")
                    break
            except Exception:
                pass

        if search_input is None:
            # Broad fallback: any visible text input near the top of the page
            try:
                inputs = page.locator("input[type='text']").all()
                for inp in inputs:
                    if inp.is_visible():
                        search_input = inp
                        self.log("Using fallback: first visible text input")
                        break
            except Exception:
                pass

        if search_input is None:
            self.log("Could not find search input on HQEW homepage.", logging.ERROR)
            result["渠道链接"] = page.url
            result["货期"] = "未找到搜索框"
            return result

        # Clear existing text and type the model
        try:
            search_input.click()
            search_input.fill("")
            time.sleep(0.3)
            search_input.fill(model)
            time.sleep(0.5)
            self.log(f"Typed model '{model}' into search input.")
        except Exception as e:
            self.log(f"Failed to type in search input: {e}", logging.ERROR)
            result["渠道链接"] = page.url
            result["货期"] = f"搜索输入失败: {str(e)[:30]}"
            return result

        # Step 3: Click the search button
        search_btn = None
        btn_selectors = [
            "button:has-text('搜索')",
            "input[value='搜索']",
            "button.search-btn",
            ".search-button",
            "a:has-text('搜索')",
            "button[type='submit']",
            ".search-box button",
        ]
        for sel in btn_selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible():
                    search_btn = el
                    self.log(f"Found search button with selector: {sel}")
                    break
            except Exception:
                pass

        if search_btn:
            try:
                search_btn.click()
            except Exception as e:
                self.log(f"Search button click failed, trying Enter key: {e}", logging.WARNING)
                search_input.press("Enter")
        else:
            self.log("Search button not found, pressing Enter instead.")
            search_input.press("Enter")

        page.wait_for_timeout(3000)
        result["渠道链接"] = page.url
        self.log(f"Search results page URL: {page.url}")

        # Step 4: Extract results from the search results page
        try:
            extracted = self._try_table_extraction(page, model, quantity)
            if extracted:
                return extracted

            extracted = self._try_card_extraction(page, model, quantity)
            if extracted:
                return extracted

            extracted = self._try_body_text_extraction(page, model, quantity)
            if extracted:
                return extracted

            self.log(f"Model {model} not found or no price on HQEW.")
            result["货期"] = "未找到型号"

        except Exception as e:
            self.log(f"Error parsing HQEW search results: {e}", logging.ERROR)
            result["货期"] = f"解析失败: {str(e)[:40]}"

        return result

    # ─── extraction strategies ───

    def _try_table_extraction(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """
        Parse the B2B supplier table that HQEW shows.
        Columns (from screenshot): 供应商 | 型号 | 品牌 | 数量 | 批号 | 封装 | 仓库 | 交易说明 | 日期 | 询价
        The price column shows "云价格" at the top and ¥ values per row.
        """
        rows = page.locator("tr").all()
        if len(rows) < 2:
            return None

        self.log(f"Found {len(rows)} table rows on the search listings page.")
        extracted_prices: List[Dict] = []
        clean_model = re.sub(r'\s+', '', model).lower()

        for row in rows:
            try:
                cells = row.locator("td").all()
                if len(cells) < 5:
                    continue

                # Try to identify model match in any cell
                row_text = row.inner_text()
                clean_row = re.sub(r'\s+', '', row_text).lower()
                if clean_model not in clean_row:
                    continue

                # Extract brand – usually in a cell containing known brand patterns
                brand = ""
                stock_str = ""
                price_val = None

                for cell in cells:
                    txt = cell.inner_text().strip()

                    # Brand detection (e.g., "YAGEO/国巨", "VISHAY/威世")
                    if "/" in txt and len(txt) < 30 and not txt.startswith("http"):
                        if not brand:
                            brand = txt

                    # Price detection
                    price_match = re.search(r'[￥¥$]\s*(\d+\.?\d*)', txt)
                    if price_match and price_val is None:
                        price_val = float(price_match.group(1))

                    # Stock / quantity detection (pure number)
                    qty_match = re.match(r'^[\d,]+\+?$', txt.replace(" ", ""))
                    if qty_match and not stock_str:
                        stock_str = txt.replace(" ", "")

                # If no price found with currency symbol, look for float in row
                if price_val is None:
                    all_floats = re.findall(r'(\d+\.\d{2,})', row_text)
                    if all_floats:
                        price_val = float(all_floats[0])

                if price_val is not None and price_val > 0:
                    stock_num = 0
                    m = re.search(r'(\d[\d,]*)', stock_str)
                    if m:
                        stock_num = int(m.group(1).replace(",", ""))

                    extracted_prices.append({
                        "brand": brand,
                        "stock": stock_num,
                        "price": price_val,
                    })
            except Exception:
                pass

        if not extracted_prices:
            return None

        sorted_prices = sorted(extracted_prices, key=lambda x: x["price"])
        best = sorted_prices[0]
        result = self.get_empty_result(model, quantity)
        result["渠道链接"] = page.url
        result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result["品牌"] = best["brand"] or "未显示"
        result["库存数量"] = str(best["stock"]) if best["stock"] > 0 else "有货"
        result["货期"] = "现货"

        cny_price, orig_price_str = self.convert_price_to_cny(str(best["price"]), "CNY")
        result["适用价格(人民币)"] = cny_price
        result["原始币种价格"] = orig_price_str
        self.log(f"Table extraction: lowest price {cny_price} CNY")
        return result

    def _try_card_extraction(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """Try extracting from product cards / list items."""
        card_selectors = [
            ".product-item", ".goods-item", ".search-result-item",
            ".product-card", "[class*='product']",
        ]
        clean_model = re.sub(r'\s+', '', model).lower()

        for sel in card_selectors:
            cards = page.locator(sel).all()
            for card in cards:
                try:
                    text = card.inner_text()
                    if clean_model not in re.sub(r'\s+', '', text).lower():
                        continue
                    m = re.search(r'[￥¥$]?\s*(\d+\.\d+)', text)
                    if m:
                        price_val = float(m.group(1))
                        result = self.get_empty_result(model, quantity)
                        result["渠道链接"] = page.url
                        result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        result["货期"] = "现货"
                        cny_price, orig = self.convert_price_to_cny(str(price_val), "CNY")
                        result["适用价格(人民币)"] = cny_price
                        result["原始币种价格"] = orig
                        self.log(f"Card extraction: price {cny_price} CNY")
                        return result
                except Exception:
                    pass

        return None

    def _try_body_text_extraction(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """Last-resort: scan full body text for the model and a nearby price."""
        try:
            body = page.locator("body").inner_text()
            clean_model = re.sub(r'\s+', '', model).lower()
            if clean_model not in re.sub(r'\s+', '', body).lower():
                return None

            prices = re.findall(r'[￥¥$]?\s*(\d+\.\d{2,})', body)
            if prices:
                price_val = min(float(p) for p in prices)
                result = self.get_empty_result(model, quantity)
                result["渠道链接"] = page.url
                result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                result["货期"] = "现货"
                cny_price, orig = self.convert_price_to_cny(str(price_val), "CNY")
                result["适用价格(人民币)"] = cny_price
                result["原始币种价格"] = orig
                self.log(f"Body-text extraction: price {cny_price} CNY")
                return result
        except Exception:
            pass
        return None
