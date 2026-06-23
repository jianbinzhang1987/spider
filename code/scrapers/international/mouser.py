import time
import logging
from datetime import datetime
from typing import Dict, Any, List
import re
from playwright.sync_api import Page, BrowserContext
from scrapers.base_scraper import BaseScraper

class MouserScraper(BaseScraper):
    def __init__(self, headless: bool = False):
        super().__init__("mouser", headless)

    def login(self, context: BrowserContext, page: Page) -> bool:
        """
        Logs into Mouser.
        """
        self.log(f"Navigating to login page: {self.login_url}")
        page.goto(self.login_url)
        page.wait_for_load_state("networkidle")

        if "Login" not in page.url and "login" not in page.url.lower():
            self.log("Already logged in (based on URL).")
            return True

        try:
            # Fill login form
            username_input = page.locator("input[id*='Username'], input[name*='Username'], input[type='text']").first
            password_input = page.locator("input[id*='Password'], input[name*='Password'], input[type='password']").first
            
            username_input.fill(self.username)
            password_input.fill(self.password)
            time.sleep(0.5)
            
            # Click submit
            submit_btn = page.locator("button[type='submit'], input[type='submit'], button:has-text('登录')").first
            submit_btn.click()
            
            page.wait_for_timeout(4000)
            
            # Handle captcha/Cloudflare
            if page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .cf-turnstile-wrapper").first.is_visible() or "login" in page.url.lower():
                self.wait_for_human_intervention(
                    page, 
                    "检测到登录安全验证，请在浏览器中手动完成登录或滑块。",
                    "text=我的账户, text=退出, a[href*='SignOut']"
                )
                
            if "login" not in page.url.lower():
                self.log("Login succeeded.")
                self.save_session(context)
                return True
            else:
                if page.locator("text=我的账户, text=退出").first.is_visible():
                    self.log("Found account indicators. Login succeeded.")
                    self.save_session(context)
                    return True
                    
        except Exception as e:
            self.log(f"Login error: {e}", logging.ERROR)
            
        return False

    def search_and_extract(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """
        Search for model on Mouser and extract data.
        """
        search_url = f"https://www.mouser.cn/c/?q={model}"
        self.log(f"Searching for model: {model} using URL: {search_url}")
        
        self.safe_goto(page, search_url)
        page.wait_for_timeout(2500)

        # Check if redirected directly to detail page
        # Mouser detail page URLs typically contain "/ProductDetail/"
        if "/ProductDetail/" in page.url or "/productdetail/" in page.url.lower():
            self.log("Directly redirected to product detail page.")
            return self._extract_details(page, model, quantity)

        # On search results list page
        # Find product links matching "/ProductDetail/"
        product_links = page.locator("a[href*='/ProductDetail/'], a[href*='/productdetail/']").all()
        if not product_links:
            product_links = page.locator(".mousers-link, .product-links a").all()
            
        target_url = None
        for link in product_links:
            href = link.get_attribute("href")
            text = link.inner_text().strip()
            
            clean_text = re.sub(r'\s+', '', text).lower()
            clean_model = re.sub(r'\s+', '', model).lower()
            
            if clean_model in clean_text or clean_text in clean_model:
                if href:
                    if href.startswith("//"):
                        target_url = f"https:{href}"
                    elif href.startswith("/"):
                        target_url = f"https://www.mouser.cn{href}"
                    else:
                        target_url = href
                    break
                    
        if not target_url and product_links:
            href = product_links[0].get_attribute("href")
            if href:
                if href.startswith("//"):
                    target_url = f"https:{href}"
                elif href.startswith("/"):
                    target_url = f"https://www.mouser.cn{href}"
                else:
                    target_url = href
                self.log(f"No exact match text found. Falling back to first result: {target_url}")

        if not target_url:
            self.log(f"Model {model} not found in search results.")
            return self.get_empty_result(model, quantity)

        self.log(f"Navigating to detail page: {target_url}")
        self.safe_goto(page, target_url)
        page.wait_for_timeout(1500)
        
        return self._extract_details(page, model, quantity)

    def _extract_details(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """
        Extract detailed information from Mouser product detail page.
        """
        result = self.get_empty_result(model, quantity)
        result["渠道链接"] = page.url
        result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Fill purchase quantity on the page first
        try:
            qty_input = page.locator("#multqty, #txtQty, input[id*='Quantity'], input[name*='qty']").first
            if qty_input.is_visible():
                qty_input.fill(str(quantity))
                page.keyboard.press("Enter")
                page.wait_for_timeout(1000)
                self.log(f"Filled quantity {quantity} into Mouser input.")
        except Exception as e:
            self.log(f"Failed to fill quantity: {e}", logging.WARNING)

        try:
            # 1. Extract Brand
            brand_val = ""
            brand_selectors = [
                "#spnManufacturerName", 
                "a[id*='Manufacturer']", 
                "[data-testid='manufacturer-name']",
                "text=制造商："
            ]
            for selector in brand_selectors:
                el = page.locator(selector).first
                if el.is_visible():
                    txt = el.inner_text().strip()
                    if "制造商：" in txt:
                        txt = txt.replace("制造商：", "")
                    if txt:
                        brand_val = txt
                        break
            result["品牌"] = brand_val or "未显示"

            # 2. Extract Stock
            stock_val = "未显示"
            stock_selectors = [
                "#spnStock", 
                ".stock-status", 
                ".pdp-stock-status",
                "text=有库存："
            ]
            for selector in stock_selectors:
                el = page.locator(selector).first
                if el.is_visible():
                    txt = el.inner_text().strip()
                    if "有库存：" in txt:
                        txt = txt.split("有库存：")[-1]
                    stock_val = txt.strip()
                    break
            result["库存数量"] = stock_val

            # 3. Extract Lead Time
            lead_val = "现货"
            lead_selectors = [
                "#spnFactoryLeadTime", 
                "text=工厂前置时间", 
                "text=出厂前置时间"
            ]
            for selector in lead_selectors:
                el = page.locator(selector).first
                if el.is_visible():
                    txt = el.inner_text().strip()
                    if "工厂前置时间：" in txt:
                        txt = txt.split("工厂前置时间：")[-1]
                    if "出厂前置时间：" in txt:
                        txt = txt.split("出厂前置时间：")[-1]
                    lead_val = txt.strip()
                    break
            result["货期"] = lead_val

            # 4. Extract Price Grid
            # Mouser pricing tables usually contain columns like "数量" and "单价"
            prices = []
            currency = "USD"  # default
            
            # Find pricing table rows
            price_rows = page.locator(".pricing-table tr, .price-break tr, table[class*='price'] tr, #pdp-pricing-table tr").all()
            for row in price_rows:
                text = re.sub(r'\s+', ' ', row.inner_text().strip())
                # Check for currency symbols in the row text to detect currency
                if "¥" in text or "CNY" in text:
                    currency = "CNY"
                elif "$" in text or "USD" in text:
                    currency = "USD"
                    
                # Match pattern: e.g. "10 $2.50" or "10 ¥18.00"
                # Quantity (number) then optional spaces/text then price (digits with dot)
                matches = re.findall(r'(\d+[\d,]*)\+?.*?([¥$]?\s*\d+\.\d+)', text)
                for qty_str, price_str in matches:
                    try:
                        qty = int(qty_str.replace(",", ""))
                        price_val = float(price_str.replace("¥", "").replace("$", "").strip())
                        prices.append((qty, price_val))
                    except Exception:
                        pass
            
            # Sort prices by quantity
            prices = sorted(list(set(prices)), key=lambda x: x[0])
            self.log(f"Extracted price tiers: {prices} (Currency: {currency})")

            if prices:
                applicable_price = None
                note = ""
                for tier_qty, tier_price in reversed(prices):
                    if quantity >= tier_qty:
                        applicable_price = tier_price
                        break
                if applicable_price is None:
                    applicable_price = prices[0][1]
                    note = f"(最小起购量为 {prices[0][0]}, 取此档价格)"
                
                cny_price, orig_price_str = self.convert_price_to_cny(str(applicable_price), currency)
                result["适用价格(人民币)"] = cny_price
                result["原始币种价格"] = f"{orig_price_str} {note}".strip()
            else:
                # Fallback to single price element
                single_price_el = page.locator("#spnPrice, .price-num, .pdp-price").first
                if single_price_el.is_visible():
                    txt = single_price_el.inner_text().strip()
                    if "¥" in txt or "CNY" in txt:
                        currency = "CNY"
                    cny_price, orig_price_str = self.convert_price_to_cny(txt, currency)
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
