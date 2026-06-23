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
        """Search for the model on HQEW and extract information."""
        # Use the standard search page instead of the B2B direct-URL pattern
        # which was constructing an invalid URL (so.hqew.com/product/...).
        encoded_model = quote(model, safe='')
        search_url = f"https://so.hqew.com/product/{encoded_model}"
        self.log(f"Searching for model: {model} using URL: {search_url}")

        self.safe_goto(page, search_url)
        page.wait_for_timeout(3000)

        result = self.get_empty_result(model, quantity)
        result["渠道链接"] = page.url
        result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            # ── Strategy 1: B2B table rows (original approach, kept as primary) ──
            extracted = self._try_table_extraction(page, model, quantity)
            if extracted:
                return extracted

            # ── Strategy 2: product-card / list-item style results ──
            extracted = self._try_card_extraction(page, model, quantity)
            if extracted:
                return extracted

            # ── Strategy 3: body text regex ──
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

                # Try to find part number in any cell
                part_no = ""
                brand = ""
                stock_str = ""
                price_val = None

                for idx, cell in enumerate(cells):
                    txt = " ".join(cell.inner_text().split())
                    clean_txt = re.sub(r'\s+', '', txt).lower()

                    if clean_model in clean_txt or clean_txt in clean_model:
                        part_no = txt

                    if re.search(r'[￥¥$]\s*\d+\.\d+', txt):
                        m = re.search(r'[￥¥$]?\s*(\d+\.\d+)', txt)
                        if m:
                            price_val = float(m.group(1))

                    if re.match(r'^[\d,]+$', txt.replace(" ", "")):
                        stock_str = txt

                if not part_no:
                    # check if any cell matches model (wider approach)
                    all_text = " ".join(c.inner_text() for c in cells)
                    if clean_model not in re.sub(r'\s+', '', all_text).lower():
                        continue

                # Try extracting brand from cell at typical positions
                if len(cells) > 4:
                    brand = " ".join(cells[4].inner_text().split()) if not brand else brand
                if not brand and len(cells) > 2:
                    brand = " ".join(cells[2].inner_text().split())

                stock_num = 0
                m = re.search(r'(\d[\d,]*)', stock_str)
                if m:
                    stock_num = int(m.group(1).replace(",", ""))

                if price_val is None:
                    # last resort: find any float in the row
                    row_text = row.inner_text()
                    m = re.search(r'(\d+\.\d+)', row_text)
                    if m:
                        price_val = float(m.group(1))

                if price_val is not None:
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
