import time
import logging
from datetime import datetime
from typing import Dict, Any, List
import re
from playwright.sync_api import Page, BrowserContext
from scrapers.base_scraper import BaseScraper

class HqewScraper(BaseScraper):
    def __init__(self, headless: bool = False):
        super().__init__("hqew", headless)

    def login(self, context: BrowserContext, page: Page) -> bool:
        """
        Logs into 华强电子网 (HQEW).
        """
        self.log(f"Navigating to login page: {self.login_url}")
        page.goto(self.login_url)
        page.wait_for_load_state("networkidle")

        if "login" not in page.url:
            self.log("Already logged in (based on URL redirect).")
            return True

        try:
            # Check for standard password login tab
            pwd_tab = page.locator("text=密码登录, text=账号登录, .login-type-pwd").first
            if pwd_tab.is_visible():
                pwd_tab.click()
                time.sleep(0.5)

            # Fill username and password
            username_input = page.locator("input[placeholder*='用户名'], input[placeholder*='手机'], input[placeholder*='账号']").first
            password_input = page.locator("input[type='password']").first
            
            username_input.fill(self.username)
            password_input.fill(self.password)
            time.sleep(0.5)
            
            # Click Login button
            submit_btn = page.locator("button:has-text('登录'), .btn-login, input[type='button']").first
            submit_btn.click()
            
            page.wait_for_timeout(3000)
            
            # Handle captcha/slider
            if page.locator(".geetest_radar_btn, .geetest_slider_button, iframe[src*='geetest']").first.is_visible() or "login" in page.url:
                self.wait_for_human_intervention(
                    page, 
                    "检测到登录验证，请在浏览器中手动完成登录。",
                    "text=退出, .user-center, a[href*='logout']"
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
        Search for the model on HQEW and extract information.
        """
        # Formulate search URL using s.hqew.com B2B listings page
        search_url = f"https://s.hqew.com/{model}_______0_{quantity}_0_1.html"
        self.log(f"Searching for model: {model} using URL: {search_url}")
        
        self.safe_goto(page, search_url)
        page.wait_for_timeout(2000)

        result = self.get_empty_result(model, quantity)
        result["渠道链接"] = page.url
        result["查询时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            rows = page.locator("tr").all()
            self.log(f"Found {len(rows)} table rows on the search listings page.")
            
            extracted_prices = []
            
            for row in rows:
                try:
                    cells = row.locator("td").all()
                    if len(cells) < 10:
                        continue
                        
                    supplier = " ".join(cells[1].inner_text().split())
                    part_no = " ".join(cells[3].inner_text().split())
                    brand = " ".join(cells[4].inner_text().split())
                    qty_str = " ".join(cells[6].inner_text().split())
                    desc = " ".join(cells[9].inner_text().split())
                    
                    # Verify model match
                    clean_part = re.sub(r'\s+', '', part_no).lower()
                    clean_model = re.sub(r'\s+', '', model).lower()
                    
                    if clean_model not in clean_part and clean_part not in clean_model:
                        continue
                        
                    # Extract stock quantity
                    stock_num = 0
                    qty_match = re.search(r'(\d+[\d,]*)', qty_str)
                    if qty_match:
                        stock_num = int(qty_match.group(1).replace(",", ""))
                        
                    # Extract price from description
                    price_val = None
                    price_match = re.search(r'[￥¥$]\s*(\d+\.\d+)', desc)
                    if price_match:
                        price_val = float(price_match.group(1))
                    else:
                        float_match = re.search(r'\b(\d+\.\d+)\b', desc)
                        if float_match:
                            price_val = float(float_match.group(1))
                            
                    if price_val is not None:
                        extracted_prices.append({
                            "supplier": supplier,
                            "brand": brand,
                            "stock": stock_num,
                            "price": price_val,
                            "raw_desc": desc
                        })
                except Exception:
                    pass
            
            if extracted_prices:
                # Sort by price ascending
                sorted_prices = sorted(extracted_prices, key=lambda x: x["price"])
                best = sorted_prices[0]
                
                result["品牌"] = best["brand"] or "未显示"
                result["库存数量"] = str(best["stock"]) if best["stock"] > 0 else "有货"
                result["货期"] = "现货"
                
                cny_price, orig_price_str = self.convert_price_to_cny(str(best["price"]), "CNY")
                result["适用价格(人民币)"] = cny_price
                result["原始币种价格"] = f"{orig_price_str} (自商户: {best['supplier']})".strip()
                self.log(f"Successfully selected lowest B2B price: {cny_price} CNY from {best['supplier']}")
            else:
                # Fallback: if no price found but we have rows, grab the first row info
                found_fallback = False
                for row in rows:
                    try:
                        cells = row.locator("td").all()
                        if len(cells) < 10:
                            continue
                        part_no = " ".join(cells[3].inner_text().split())
                        clean_part = re.sub(r'\s+', '', part_no).lower()
                        clean_model = re.sub(r'\s+', '', model).lower()
                        if clean_model in clean_part or clean_part in clean_model:
                            result["品牌"] = " ".join(cells[4].inner_text().split()) or "未显示"
                            result["库存数量"] = " ".join(cells[6].inner_text().split()) or "有货"
                            result["货期"] = "无价格信息"
                            result["原始币种价格"] = "未显示价格"
                            found_fallback = True
                            break
                    except Exception:
                        pass
                if not found_fallback:
                    self.log(f"Model {model} not found in search results.")
                    result["货期"] = "未找到型号"
        except Exception as e:
            self.log(f"Error parsing HQEW search results: {e}", logging.ERROR)
            result["货期"] = f"解析失败: {str(e)[:40]}"
            
        return result

