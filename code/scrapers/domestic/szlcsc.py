import time
import logging
from datetime import datetime
from typing import Dict, Any, List
import re
from playwright.sync_api import Page, BrowserContext
from scrapers.base_scraper import BaseScraper

class SzlcscScraper(BaseScraper):
    def __init__(self, headless: bool = False):
        super().__init__("szlcsc", headless)

    def login(self, context: BrowserContext, page: Page) -> bool:
        """
        Logs into 立创商城 (SZLCSC).
        """
        self.log(f"Navigating to login page: {self.login_url}")
        page.goto(self.login_url)
        page.wait_for_load_state("networkidle")
        
        # Check if already logged in (redirected to user center or main page)
        if "login" not in page.url:
            self.log("Already logged in (based on URL redirect).")
            return True

        # Locate username and password fields
        try:
            # Check for standard password login tab first if there are tabs
            # Sometimes there's phone login vs account login
            password_login_tab = page.locator("text=密码登录").first
            if password_login_tab.is_visible():
                password_login_tab.click()
                time.sleep(0.5)

            # Fill credentials
            # Selectors can be input[type="text"], input[type="password"] or placeholdered ones
            username_input = page.locator("input[placeholder*='手机'], input[placeholder*='账号'], input[placeholder*='邮箱']").first
            password_input = page.locator("input[type='password']").first
            
            username_input.fill(self.username)
            password_input.fill(self.password)
            time.sleep(0.5)
            
            # Click submit button
            submit_btn = page.locator("button:has-text('登录'), input[type='submit'], .btn-login").first
            submit_btn.click()
            
            # Wait for navigation or verification
            # Since there could be a slider verification, we wait
            page.wait_for_timeout(3000)
            
            # Check if slider or captcha is present
            if page.locator(".geetest_radar_btn, .geetest_slider_button, iframe[src*='geetest']").first.is_visible() or "login" in page.url:
                self.wait_for_human_intervention(
                    page, 
                    "检测到登录验证码/滑块，请在弹出的浏览器中手动完成登录。",
                    "a:has-text('退出'), .user-name, div[class*='user']"  # Selector that appears after successful login
                )
            
            # Check if login succeeded (URL does not contain login or user element is present)
            if "login.html" not in page.url:
                self.log("Login succeeded.")
                self.save_session(context)
                return True
            else:
                self.log("Login page still active, checking user indicator...")
                # Fallback: check if we can see logout or user center
                if page.locator("text=退出, text=个人中心").first.is_visible():
                    self.log("User indicators found. Login succeeded.")
                    self.save_session(context)
                    return True
                
        except Exception as e:
            self.log(f"Login error: {e}", logging.ERROR)
            
        return False

    def search_and_extract(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """
        Search for the model on SZLCSC and extract information.
        """
        search_url = f"https://so.szlcsc.com/global.html?k={model}"
        self.log(f"Searching for model: {model} using URL: {search_url}")
        
        self.safe_goto(page, search_url)
        page.wait_for_timeout(2000)  # Wait for AJAX results

        # 1. Determine if we are on a list page or directly redirected to a detail page
        # Detail pages typically have "/product/details_" or "item.szlcsc.com" in their URL
        if "/product/details_" in page.url or "item.szlcsc.com" in page.url:
            self.log("Directly redirected to product detail page.")
            return self._extract_details(page, model, quantity)

        # 2. We are on the search results list page. Find the exact matching product.
        # Find all detail links. Often they contain "/product/details_" or are elements in a product grid.
        # Let's locate links matching `/product/details_` or `item.szlcsc.com`
        product_links = page.locator("a[href*='item.szlcsc.com'], a[href*='/product/details_']").all()
        if not product_links:
            # Try finding by general class
            product_links = page.locator(".product-title, .product-item a").all()
            
        target_url = None
        for link in product_links:
            href = link.get_attribute("href")
            text = link.inner_text().strip()
            
            # Check if this link matches the model name exactly (case insensitive, ignoring whitespace)
            clean_text = re.sub(r'\s+', '', text).lower()
            clean_model = re.sub(r'\s+', '', model).lower()
            
            if clean_model in clean_text or clean_text in clean_model:
                if href:
                    if href.startswith("//"):
                        target_url = f"https:{href}"
                    elif href.startswith("/"):
                        target_url = f"https://so.szlcsc.com{href}"
                    else:
                        target_url = href
                    break
        
        # Fallback: if no exact match text found, just take the first result's detail link
        if not target_url and product_links:
            href = product_links[0].get_attribute("href")
            if href:
                if href.startswith("//"):
                    target_url = f"https:{href}"
                elif href.startswith("/"):
                    target_url = f"https://so.szlcsc.com{href}"
                else:
                    target_url = href
                self.log(f"No exact match text found. Falling back to first result: {target_url}")

        if not target_url:
            self.log(f"Model {model} not found in search results.")
            return self.get_empty_result(model, quantity)

        # Navigate to product detail page
        self.log(f"Navigating to detail page: {target_url}")
        self.safe_goto(page, target_url)
        page.wait_for_timeout(1500)
        
        return self._extract_details(page, model, quantity)

    def _extract_details(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """
        Helper to extract details from the product detail page.
        """
        result = self.get_empty_result(model, quantity)
        result["渠道链接"] = page.url
        result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Fill purchase quantity on the page first
        try:
            qty_input = page.locator("input.file\\:text-foreground, input:not([id*='search']):not([class*='search'])").first
            if qty_input.is_visible():
                qty_input.fill(str(quantity))
                page.keyboard.press("Enter")
                page.wait_for_timeout(1000)
                self.log(f"Filled quantity {quantity} into LCSC input.")
        except Exception as e:
            self.log(f"Failed to fill quantity: {e}", logging.WARNING)

        try:
            # 1. Extract Brand
            # Often found near a element containing "品牌"
            brand_val = ""
            brand_selectors = [
                "td:has-text('品牌') + td", 
                ".brand-name", 
                "a[href*='brand']", 
                "text=品牌："
            ]
            for selector in brand_selectors:
                el = page.locator(selector).first
                if el.is_visible():
                    txt = el.inner_text().strip()
                    # Clean up label if found in the text itself
                    if "品牌：" in txt:
                        txt = txt.replace("品牌：", "")
                    if txt:
                        brand_val = txt
                        break
            result["品牌"] = brand_val or "未显示"

            # 2. Extract Stock / Inventory
            # Look for stock elements
            stock_val = "未显示"
            stock_selectors = [
                ".stock-num", 
                "text=立即发货：", 
                "text=库存：", 
                "td:has-text('库存') + td"
            ]
            for selector in stock_selectors:
                el = page.locator(selector).first
                if el.is_visible():
                    txt = el.inner_text().strip()
                    if "立即发货：" in txt:
                        txt = txt.split("立即发货：")[-1]
                    if "库存：" in txt:
                        txt = txt.split("库存：")[-1]
                    # Extract numeric stock or text
                    stock_val = txt.strip()
                    break
            result["库存数量"] = stock_val

            # 3. Extract Lead Time / 货期
            lead_val = "现货"
            lead_selectors = [
                "text=发货时间：", 
                "text=货期：", 
                "td:has-text('货期') + td"
            ]
            for selector in lead_selectors:
                el = page.locator(selector).first
                if el.is_visible():
                    txt = el.inner_text().strip()
                    if "发货时间：" in txt:
                        txt = txt.split("发货时间：")[-1]
                    if "货期：" in txt:
                        txt = txt.split("货期：")[-1]
                    lead_val = txt.strip()
                    break
            result["货期"] = lead_val

            # 4. Extract Price Grid
            # Price grids on SZLCSC typically list quantity ranges and corresponding prices.
            # Selectors for price rows: e.g. `.price-list tr`, or looking for element text with ¥/元
            prices = []  # List of tuples: (min_qty, unit_price)
            
            # Let's inspect potential price grid rows
            price_rows = page.locator(".price-list tr, table.price-table tr, .ladder-price-item, .price-range-item").all()
            
            if not price_rows:
                # If no clear row elements, search page for currency symbol followed by numbers
                # and extract near quantity labels
                # Let's try standard selectors
                price_text_blocks = page.locator(".price-grid, .ladder-price, table").all_inner_texts()
                self.log(f"Trying fallback price table parse. Total tables/grids found: {len(price_text_blocks)}")
                
            for row in price_rows:
                text = re.sub(r'\s+', ' ', row.inner_text().strip())
                # A price row typically contains something like "1-9" or "1+" and "¥1.50" or "1.50"
                # Let's parse all numbers and prices
                # Clean up commas and currency symbols
                matches = re.findall(r'(\d+)\+?.*?([¥$]?\s*\d+\.\d+)', text)
                for qty_str, price_str in matches:
                    try:
                        qty = int(qty_str)
                        price_val = float(price_str.replace("¥", "").replace("$", "").strip())
                        prices.append((qty, price_val))
                    except Exception:
                        pass
            
            # If no prices found, try reading inputs from the standard price-list structure
            if not prices:
                # Let's search by locating class names or standard DOM structures
                # In SZLCSC, there's often elements like .price-list-qty and .price-list-price
                qtys = page.locator(".price-list .qty, .ladder-price .qty, td[class*='qty']").all_inner_texts()
                vals = page.locator(".price-list .price, .ladder-price .price, td[class*='price']").all_inner_texts()
                for q, v in zip(qtys, vals):
                    try:
                        # Extract first number from qty
                        q_num = int(re.search(r'\d+', q).group())
                        # Extract price float
                        v_num = float(re.search(r'\d+\.\d+', v).group())
                        prices.append((q_num, v_num))
                    except Exception:
                        pass

            # Try text-based line matching from the body inner text as a fallback
            if not prices:
                try:
                    body_text = page.locator("body").inner_text()
                    lines = [l.strip() for l in body_text.split("\n") if l.strip()]
                    for i in range(len(lines) - 1):
                        line = lines[i]
                        next_line = lines[i+1]
                        qty_match = re.match(r'^([\d,]+)\+$', line)
                        price_match = re.match(r'^[￥¥$]\s*(\d+\.\d+)$', next_line)
                        if qty_match and price_match:
                            qty = int(qty_match.group(1).replace(",", ""))
                            val = float(price_match.group(1))
                            prices.append((qty, val))
                except Exception as te:
                    self.log(f"Failed parsing prices from text fallback: {te}", logging.WARNING)

            # Sort prices by quantity ascending
            prices = sorted(list(set(prices)), key=lambda x: x[0])
            self.log(f"Extracted price tiers: {prices}")

            # 5. Find applicable price based on purchase quantity
            if prices:
                applicable_price = None
                note = ""
                # Find matching range: largest tier Qty <= purchase quantity
                for tier_qty, tier_price in reversed(prices):
                    if quantity >= tier_qty:
                        applicable_price = tier_price
                        break
                
                # If quantity is smaller than the lowest tier, take the lowest tier
                if applicable_price is None:
                    applicable_price = prices[0][1]
                    note = f"(最小起购量为 {prices[0][0]}, 取此档价格)"
                
                cny_price, orig_price_str = self.convert_price_to_cny(str(applicable_price), "CNY")
                result["适用价格(人民币)"] = cny_price
                result["原始币种价格"] = f"{orig_price_str} {note}".strip()
            else:
                # No price tiers found, try single price
                single_price_el = page.locator(".price-num, .price, .product-price").first
                if single_price_el.is_visible():
                    txt = single_price_el.inner_text().strip()
                    cny_price, orig_price_str = self.convert_price_to_cny(txt, "CNY")
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
