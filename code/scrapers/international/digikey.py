import time
import logging
from datetime import datetime
from typing import Dict, Any, List
import re
from urllib.parse import quote
from playwright.sync_api import Page, BrowserContext
from scrapers.base_scraper import BaseScraper


class DigikeyScraper(BaseScraper):
    def __init__(self, headless: bool = False):
        super().__init__("digikey", headless)

    def login(self, context: BrowserContext, page: Page) -> bool:
        """Logs into Digi-Key."""
        self.log(f"Navigating to login page: {self.login_url}")
        try:
            page.goto(self.login_url, timeout=30000)
            page.wait_for_load_state("networkidle")
        except Exception as e:
            self.log(f"Login page load failed: {e}", logging.WARNING)
            return False

        if "login" not in page.url.lower():
            self.log("Already logged in (based on URL redirect).")
            return True

        try:
            username_input = page.locator(
                "input[type='email'], input[name*='username'], input[id*='username'], "
                "input[name*='email'], input[id*='email']"
            ).first
            password_input = page.locator(
                "input[type='password'], input[name*='password'], input[id*='password']"
            ).first

            username_input.fill(self.username)
            password_input.fill(self.password)
            time.sleep(0.5)

            submit_btn = page.locator(
                "button[type='submit'], #submit-btn, button:has-text('登录'), "
                "button:has-text('Sign In'), button:has-text('Log In')"
            ).first
            submit_btn.click()

            page.wait_for_timeout(4000)

            if page.locator(
                "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .cf-turnstile-wrapper"
            ).first.is_visible() or "login" in page.url.lower():
                self.wait_for_human_intervention(
                    page,
                    "检测到登录安全验证，请在浏览器中手动完成登录或滑块。",
                    "text=我的账户, text=退出, a[href*='logout'], text=My Account",
                )

            if "login" not in page.url.lower():
                self.log("Login succeeded.")
                self.save_session(context)
                return True
            else:
                if page.locator("text=退出, text=退出登录, text=My Account").first.is_visible():
                    self.log("Found logout indicators. Login succeeded.")
                    self.save_session(context)
                    return True

        except Exception as e:
            self.log(f"Login error: {e}", logging.ERROR)

        return False

    def search_and_extract(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """Search for model on Digi-Key and extract details."""
        encoded_model = quote(model, safe='')
        search_url = f"https://www.digikey.cn/zh/products?keywords={encoded_model}"
        self.log(f"Searching for model: {model} using URL: {search_url}")

        # Use a gentle navigation approach – catch connection errors gracefully
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

        # Check if page is a verification / block page
        try:
            page_text = page.locator("body").inner_text()
            block_phrases = [
                "访问暂时受限", "Access Denied", "403 Forbidden",
                "Just a moment", "Verify you are human",
            ]
            if any(phrase in page_text for phrase in block_phrases):
                self.log("Detected access block page on Digi-Key.", logging.WARNING)
                if not self.headless:
                    self.handle_captcha_or_block(page, timeout_sec=120)
                else:
                    result = self.get_empty_result(model, quantity)
                    result["渠道链接"] = page.url
                    result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    result["货期"] = "访问受限(需人工验证)"
                    return result
        except Exception:
            pass

        # Check if redirected to detail page
        if "/product-detail/" in page.url or "/productdetail/" in page.url.lower():
            self.log("Directly redirected to product detail page.")
            return self._extract_details(page, model, quantity)

        # Digi-Key sometimes redirects to /zhs/models/ (datasheet page, not product)
        # These pages don't have price info, need to find the actual product link
        if "/models/" in page.url or "/zhs/models/" in page.url:
            self.log("Redirected to models/datasheet page, looking for product link...")
            prod_link = page.locator(
                "a[href*='/product-detail/'], a[href*='/zh/products/detail/']"
            ).first
            try:
                if prod_link.is_visible():
                    href = prod_link.get_attribute("href") or ""
                    target = self._normalise_url(href)
                    self.log(f"Found product link on models page: {target}")
                    self.safe_goto(page, target)
                    page.wait_for_timeout(2000)
                    return self._extract_details(page, model, quantity)
            except Exception:
                pass
            # No product link found on models page
            result = self.get_empty_result(model, quantity)
            result["渠道链接"] = page.url
            result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            result["货期"] = "仅找到数据手册页(无价格)"
            return result

        # Search results page
        product_links = page.locator(
            "a[href*='/product-detail/'], a[href*='/productdetail/']"
        ).all()
        if not product_links:
            product_links = page.locator(
                "td a[href*='/zh/products/detail/'], table a[href], .search-result a[href]"
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

        # Fallback: first result
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
        try:
            self.safe_goto(page, target_url)
        except Exception as nav_err:
            self.log(f"Detail page navigation failed: {nav_err}", logging.WARNING)
            result = self.get_empty_result(model, quantity)
            result["渠道链接"] = target_url
            result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            result["货期"] = f"详情页加载失败: {str(nav_err)[:30]}"
            return result

        page.wait_for_timeout(2000)

        return self._extract_details(page, model, quantity)

    def _normalise_url(self, href: str) -> str:
        if not href:
            return ""
        if href.startswith("//"):
            return f"https:{href}"
        if href.startswith("/"):
            return f"https://www.digikey.cn{href}"
        return href

    def _extract_details(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """Extract details from Digi-Key detail page."""
        result = self.get_empty_result(model, quantity)
        result["渠道链接"] = page.url
        result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Fill purchase quantity
        try:
            qty_input = page.locator(
                "input[data-testid='qty-input'], .qty-input, input[name='quantity'], "
                "input[id*='quantity'], input[id*='Quantity']"
            ).first
            if qty_input.is_visible():
                qty_input.fill(str(quantity))
                page.keyboard.press("Enter")
                page.wait_for_timeout(1000)
                self.log(f"Filled quantity {quantity} into Digi-Key input.")
        except Exception as e:
            self.log(f"Failed to fill quantity: {e}", logging.WARNING)

        try:
            # 1. Brand
            brand_val = self._extract_text(page, [
                "[data-testid='manufacturer-name']",
                ".manufacturer-name",
                "span:has-text('制造商')",
                "span:has-text('Manufacturer')",
                "a[href*='manufacturer']",
            ], strip_label="制造商")
            if not brand_val:
                brand_val = self._extract_from_body(page, r'(?:制造商|Manufacturer)[：:]\s*(\S+)')
            result["品牌"] = brand_val or "未显示"

            # 2. Stock
            stock_val = self._extract_text(page, [
                "[data-testid='stock-status']",
                ".stock-status",
                "span:has-text('有货')",
                "span:has-text('In Stock')",
                "span:has-text('库存')",
            ], strip_label="有货")
            result["库存数量"] = stock_val or "未显示"

            # 3. Lead Time
            lead_val = self._extract_text(page, [
                "[data-testid='factory-lead-time']",
                "span:has-text('制造商标准提前期')",
                "span:has-text('工厂前置时间')",
                "span:has-text('Factory Lead Time')",
            ], strip_label="制造商标准提前期")
            result["货期"] = lead_val or "现货"

            # 4. Price Grid
            prices, currency = self._extract_price_tiers(page)

            if prices:
                applicable_price, note = self._pick_tier_price(prices, quantity)
                cny_price, orig_price_str = self.convert_price_to_cny(str(applicable_price), currency)
                result["适用价格(人民币)"] = cny_price
                result["原始币种价格"] = f"{orig_price_str} {note}".strip()
            else:
                single = self._extract_single_price(page)
                if single is not None:
                    price_val, detected_currency = single
                    cny_price, orig_price_str = self.convert_price_to_cny(str(price_val), detected_currency)
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

    def _extract_price_tiers(self, page: Page) -> tuple:
        """Returns (list of (qty, price) tuples, currency_str)."""
        prices: List[tuple] = []
        currency = "USD"

        row_selectors = [
            ".pricing-table tr",
            ".ladder-price tr",
            "table[class*='price'] tr",
            "table tr",
        ]
        for sel in row_selectors:
            rows = page.locator(sel).all()
            for row in rows:
                text = re.sub(r'\s+', ' ', row.inner_text().strip())
                if "¥" in text or "CNY" in text or "￥" in text:
                    currency = "CNY"
                elif "$" in text or "USD" in text:
                    currency = "USD"

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

        # Fallback: separate qty/price DOM elements
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
        self.log(f"Extracted price tiers: {prices} (Currency: {currency})")
        return prices, currency

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

    def _extract_single_price(self, page: Page):
        for sel in [".price-num", ".detail-price", ".goods-price", "[class*='price']"]:
            try:
                el = page.locator(sel).first
                if el.is_visible():
                    txt = el.inner_text().strip()
                    currency = "USD"
                    if "¥" in txt or "￥" in txt or "CNY" in txt:
                        currency = "CNY"
                    m = re.search(r'(\d+\.\d+)', txt)
                    if m:
                        return float(m.group(1)), currency
            except Exception:
                pass
        return None
