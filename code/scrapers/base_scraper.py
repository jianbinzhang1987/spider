import os
import time
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from playwright.sync_api import BrowserContext, Page, Playwright
from config import CREDENTIALS, TIMEOUT, SESSIONS_DIR
from utils.browser_manager import create_browser_context, init_page, get_session_path
from utils.exchange_rate import get_usd_to_cny_rate

logger = logging.getLogger(__name__)

class BaseScraper:
    def __init__(self, site_id: str, headless: bool = False):
        self.site_id = site_id
        self.headless = headless
        
        # Load credentials
        site_config = CREDENTIALS.get(site_id, {})
        self.name = site_config.get("name", site_id)
        self.url = site_config.get("url", "")
        self.login_url = site_config.get("login_url", "")
        self.username = site_config.get("username", "")
        self.password = site_config.get("password", "")
        self.state_path = get_session_path(site_id)
        
        # Cached live exchange rate
        self._exchange_rate = None

    @property
    def exchange_rate(self) -> float:
        if self._exchange_rate is None:
            self._exchange_rate = get_usd_to_cny_rate()
        return self._exchange_rate

    def log(self, message: str, level=logging.INFO):
        logger.log(level, f"[{self.name}] {message}")

    def execute(self, playwright: Playwright, model: str, quantity: int) -> Dict[str, Any]:
        """
        Main execution flow.
        1. Launches browser with saved session if available.
        2. Tries to search and extract.
        3. If it detects login is required, executes login and retries.
        4. Closes browser and returns result.
        """
        self.log(f"Starting query for Model: {model}, Qty: {quantity}")
        context = None
        page = None
        result = self.get_empty_result(model, quantity)

        try:
            # 1. Initialize context & page
            context = create_browser_context(playwright, headless=self.headless, state_path=self.state_path)
            page = init_page(context)

            # 2. Try guest / saved session search
            try:
                result = self.search_and_extract(page, model, quantity)
                # Check if we got redirected to login or result is empty due to login required
                if result.get("_login_required", False):
                    self.log("Session expired or login required. Initiating login flow...")
                    if self.login(context, page):
                        # Retry extraction after successful login
                        result = self.search_and_extract(page, model, quantity)
                    else:
                        result["货期"] = "登录失败"
            except Exception as extract_err:
                self.log(f"Extraction error (might need login): {extract_err}", logging.WARNING)
                # Try logging in and retrying
                if self.login(context, page):
                    result = self.search_and_extract(page, model, quantity)
                else:
                    raise extract_err

        except Exception as e:
            self.log(f"Failed to query: {e}", logging.ERROR)
            result["货期"] = f"查询失败: {str(e)[:50]}"
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
                try:
                    context.browser.close()
                except Exception:
                    pass
        
        # Remove internal control flags
        if "_login_required" in result:
            del result["_login_required"]

        return result

    def login(self, context: BrowserContext, page: Page) -> bool:
        """
        Execute login. Should be overridden by subclasses.
        Saves session state on success.
        """
        self.log("Login method not implemented in base class.")
        return False

    def search_and_extract(self, page: Page, model: str, quantity: int) -> Dict[str, Any]:
        """
        Execute search and data extraction. Should be overridden by subclasses.
        """
        raise NotImplementedError("Subclasses must implement search_and_extract")

    def save_session(self, context: BrowserContext):
        """
        Persist browser session storage.
        """
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            context.storage_state(path=self.state_path)
            self.log(f"Session saved successfully to {self.state_path}")
        except Exception as e:
            self.log(f"Failed to save session: {e}", logging.WARNING)

    def get_empty_result(self, model: str, quantity: int) -> Dict[str, Any]:
        """
        Default result dictionary with placeholders.
        """
        return {
            "型号": model,
            "品牌": "",
            "采购数量": quantity,
            "适用价格(人民币)": None,
            "库存数量": "未显示",
            "货期": "未找到型号",
            "来源网站": self.name,
            "渠道链接": self.url,
            "原始币种价格": "无",
            "查询时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    def convert_price_to_cny(self, price_str: str, original_currency: str = "USD") -> tuple:
        """
        Helper to convert foreign currencies to CNY.
        Returns (cny_price, original_price_str)
        """
        try:
            # Clean non-numeric characters except decimal point
            clean_price = "".join(c for c in price_str if c.isdigit() or c == '.')
            price_val = float(clean_price)
            
            if original_currency.upper() == "USD":
                cny_price = round(price_val * self.exchange_rate, 4)
                return cny_price, f"{price_val:.4f} USD"
            elif original_currency.upper() in ["CNY", "RMB", "元"]:
                return round(price_val, 4), f"{price_val:.4f} CNY"
            else:
                return price_val, f"{price_str} {original_currency}"
        except Exception as e:
            logger.warning(f"Error converting price '{price_str}': {e}")
            return None, price_str

    def wait_for_human_intervention(self, page: Page, message: str, check_selector: str, timeout_sec: int = 180):
        """
        Helper for human-in-the-loop validation.
        Brings the page to front, logs a message, and waits for a selector to appear (meaning validation passed)
        or a timeout to occur.
        """
        self.log(f"⚠️ {message}", logging.WARNING)
        page.bring_to_front()
        
        # Audio alert if possible (cross-platform visual beep / console alert)
        print("\a", end="") # standard terminal beep
        
        start_time = time.time()
        while time.time() - start_time < timeout_sec:
            try:
                # Check if check_selector is visible, indicating success
                if page.is_visible(check_selector):
                    self.log("Verification check passed! Resuming automation...")
                    return True
            except Exception:
                pass
            time.sleep(2)
        
        raise TimeoutError(f"等待人工介入校验超时（超过 {timeout_sec} 秒）")

    def handle_captcha_or_block(self, page: Page, timeout_sec: int = 180) -> bool:
        """
        Detects if the page has a captcha, Cloudflare challenge, or is blocked,
        and pauses to let the user solve it in headful mode.
        """
        if self.headless:
            return False

        is_captcha = False
        reason = ""

        try:
            title = page.title()
        except Exception:
            title = ""

        if any(kw in title for kw in ["Just a moment...", "Verify you are human", "Attention Required", "Access Denied", "Cloudflare", "Access denied", "403 Forbidden"]):
            is_captcha = True
            reason = f"安全防护页面 (Title: {title})"

        captcha_selectors = [
            "#challenge-running", 
            ".cf-turnstile", 
            "#cf-challenge-body", 
            "#px-captcha",
            ".geetest_slider", 
            ".geetest_radar", 
            "iframe[src*='geetest']",
            ".captcha-dialog",
            "iframe[src*='captcha']",
            "iframe[src*='recaptcha']",
            "iframe[src*='hcaptcha']"
        ]
        
        if not is_captcha:
            for sel in captcha_selectors:
                try:
                    if page.locator(sel).first.is_visible():
                        is_captcha = True
                        reason = f"检测到验证码元素: {sel}"
                        break
                except Exception:
                    pass

        if not is_captcha:
            try:
                body_text = page.locator("body").inner_text()
                if "Access Denied" in body_text or "403 Forbidden" in body_text or "您的请求被拒绝" in body_text or "我访问的页面不在地球了" in body_text:
                    is_captcha = True
                    reason = "网页访问被拒绝或页面不存在"
            except Exception:
                pass

        if is_captcha:
            self.log(f"⚠️ 检测到 {reason}。请在弹出的浏览器中手动完成验证/拖动滑块。", logging.WARNING)
            page.bring_to_front()
            print("\a", end="") # Terminal alert beep
            
            start_time = time.time()
            while time.time() - start_time < timeout_sec:
                time.sleep(3)
                try:
                    curr_title = page.title()
                    if not any(kw in curr_title for kw in ["Just a moment...", "Verify you are human", "Attention Required", "Access Denied", "Access denied", "403 Forbidden"]):
                        still_visible = False
                        for sel in captcha_selectors:
                            if page.locator(sel).first.is_visible():
                                still_visible = True
                                break
                        if not still_visible:
                            curr_body = page.locator("body").inner_text()
                            if "Access Denied" not in curr_body and "403 Forbidden" not in curr_body and "我访问的页面不在地球了" not in curr_body:
                                self.log("验证已通过或页面已刷新，继续运行...")
                                time.sleep(1.5)
                                return True
                except Exception:
                    pass
            
            raise TimeoutError(f"等待人工解决验证码超时（超过 {timeout_sec} 秒）")
        
        return False

    def safe_goto(self, page: Page, url: str, timeout_sec: int = 180) -> bool:
        """
        Navigate to a URL safely. If it encounters network errors or verification blocks,
        pauses to let the user resolve it.
        """
        self.log(f"Navigating to URL: {url}")
        try:
            page.goto(url, timeout=TIMEOUT)
            page.wait_for_load_state("load")
        except Exception as e:
            self.log(f"页面加载失败: {e}", logging.WARNING)
            if self.headless:
                raise e
            
            self.log(f"⚠️ 页面加载失败 ({e})。请在浏览器中检查网络、代理并手动刷新。", logging.WARNING)
            page.bring_to_front()
            print("\a", end="")
            
            start_time = time.time()
            success = False
            while time.time() - start_time < timeout_sec:
                time.sleep(3)
                try:
                    title = page.title()
                    body = page.locator("body").inner_text().strip()
                    if title and len(body) > 50 and "Access Denied" not in body and "403 Forbidden" not in body and "我访问的页面不在地球了" not in body:
                        self.log("页面加载成功，继续执行...")
                        success = True
                        break
                except Exception:
                    pass
            if not success:
                raise TimeoutError(f"等待人工刷新页面超时（超过 {timeout_sec} 秒）")
        
        # Post-navigation captcha check
        self.handle_captcha_or_block(page, timeout_sec=timeout_sec)
        return True

