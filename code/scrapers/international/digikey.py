import time
import logging
from datetime import datetime
from typing import Dict, Any, List
import re
from playwright.sync_api import Page, BrowserContext
from scrapers.base_scraper import BaseScraper

class DigikeyScraper(BaseScraper):
    def __init__(self, headless: bool = False):
        super().__init__("digikey", headless)

    def login(self, context: BrowserContext, page: Page) -> bool:
        """
        Logs into Digi-Key.
        """
        self.log(f"Navigating to login page: {self.login_url}")
        page.goto(self.login_url)
        page.wait_for_load_state("networkidle")

        if "login" not in page.url.lower():
            self.log("Already logged in (based on URL redirect).")
            return True

        try:
            # Fill credentials
            username_input = page.locator("input[type='email'], input[name*='username'], input[id*='username']").first
            password_input = page.locator("input[type='password'], input[name*='password'], input[id*='password']").first
            
            username_input.fill(self.username)
            password_input.fill(self.password)
            time.sleep(0.5)
            
            # Click Login
            submit_btn = page.locator("button[type='submit'], #submit-btn, button:has-text('登录')").first
            submit_btn.click()
            
            page.wait_for_timeout(4000)
            
            # Handle captcha or Cloudflare
            if page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .cf-turnstile-wrapper").first.is_visible() or "login" in page.url.lower():
                self.wait_for_human_intervention(
                    page, 
                    "检测到登录安全验证，请在浏览器中手动完成登录或滑块。",
                    "text=我的账户, text=退出, a[href*='logout']"
                )
                
            if "login" not in page.url.lower():
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
        Search for model on Digi-Key and extract details.
        """
        # We search digikey.cn in Chinese
        search_url = f"https://www.digikey.cn/zh/products?keywords={model}"
        self.log(f"Searching for model: {model} using URL: {search_url}")
        
        self.safe_goto(page, search_url)
        page.wait_for_timeout(2500)

        # Check if redirected directly to detail page
        # Digi-Key detail page URLs typically contain "/product-detail/"
        if "/product-detail/" in page.url or "/productdetail/" in page.url.lower():
            self.log("Directly redirected to product detail page.")
            return self._extract_details(page, model, quantity)

        # On search results list page
        product_links = page.locator("a[href*='/product-detail/'], a[href*='/productdetail/']").all()
        if not product_links:
            # Table cell links in the products list table
            product_links = page.locator("td a[href*='/zh/products/detail/']").all()
            
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
                        target_url = f"https://www.digikey.cn{href}"
                    else:
                        target_url = href
                    break
                    
        if not target_url and product_links:
            href = product_links[0].get_attribute("href")
            if href:
                if href.startswith("//"):
                    target_url = f"https:{href}"
                elif href.startswith("/"):
                    target_url = f"https://www.digikey.cn{href}"
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
        Extract details from Digi-Key detail page.
        """
        result = self.get_empty_result(model, quantity)
        result["渠道链接"] = page.url
        result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Fill purchase quantity on the page first
        try:
            qty_input = page.locator("input[data-testid='qty-input'], .qty-input, input[name='quantity']").first
            if qty_input.is_visible():
                qty_input.fill(str(quantity))
                page.keyboard.press("Enter")
                page.wait_for_timeout(1000)
                self.log(f"Filled quantity {quantity} into Digi-Key input.")
        except Exception as e:
            self.log(f"Failed to fill quantity: {e}", logging.WARNING)

        try:
            # 1. Extract Brand
            brand_val = ""
            brand_selectors = [
                "[data-testid='manufacturer-name']", 
                ".manufacturer-name", 
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
                "[data-testid='stock-status']", 
                ".stock-status",
                "text=有现货：",
                "text=立即发货"
            ]
            for selector in stock_selectors:
                el = page.locator(selector).first
                if el.is_visible():
                    txt = el.inner_text().strip()
                    if "有现货：" in txt:
                        txt = txt.split("有现货：")[-1]
                    stock_val = txt.strip()
                    break
            result["库存数量"] = stock_val

            # 3. Extract Lead Time
            lead_val = "现货"
            lead_selectors = [
                "[data-testid='factory-lead-time']", 
                "text=制造商标准前置时间", 
                "text=工厂前置时间"
            ]
            for selector in lead_selectors:
                el = page.locator(selector).first
                if el.is_visible():
                    txt = el.inner_text().strip()
                    if "制造商标准前置时间：" in txt:
                        txt = txt.split("制造商标准前置时间：")[-1]
                    if "工厂前置时间：" in txt:
                        txt = txt.split("工厂前置时间：")[-1]
                    lead_val = txt.strip()
                    break
            result["货期"] = lead_val

            # 4. Extract Price Grid
            prices = []
            currency = "USD"
            
            # Digi-Key pricing table usually has headers "数量" and "单价"
            # It's typically a table or a flex-row table
            price_rows = page.locator(".pricing-table tr, .ladder-price tr, table tr").all()
            for row in price_rows:
                text = re.sub(r'\s+', ' ', row.inner_text().strip())
                if "¥" in text or "CNY" in text:
                    currency = "CNY"
                elif "$" in text or "USD" in text:
                    currency = "USD"
                    
                matches = re.findall(r'(\d+[\d,]*)\+?.*?([¥$]?\s*\d+\.\d+)', text)
                for qty_str, price_str in matches:
                    try:
                        qty = int(qty_str.replace(",", ""))
                        price_val = float(price_str.replace("¥", "").replace("$", "").strip())
                        prices.append((qty, price_val))
                    except Exception:
                        pass

            if not prices:
                qtys = page.locator("[class*='qty'], [class*='num']").all_inner_texts()
                vals = page.locator("[class*='price']").all_inner_texts()
                for q, v in zip(qtys, vals):
                    try:
                        q_num = int(re.search(r'\d+', q).group())
                        v_num = float(re.search(r'\d+\.\d+', v).group())
                        prices.append((q_num, v_num))
                    except Exception:
                        pass

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
                single_price_el = page.locator(".price-num, .detail-price, .goods-price").first
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
