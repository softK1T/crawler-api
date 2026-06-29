import logging
import random
from typing import Optional, Dict
from urllib.parse import urlparse

from app.services.adapters.base import SiteAdapter

logger = logging.getLogger(__name__)

# Shopee login gate indicators (shown when not authenticated)
LOGIN_GATE_INDICATORS = [
    "log in to continue",
    "looks like you're not logged in",
    "page unavailable",
    "buyer/login",
]

# Selectors
USERNAME_SEL = 'input[name="loginKey"]'
PASSWORD_SEL = 'input[name="password"]'
SUBMIT_SEL = 'button[type="submit"]'
SUCCESS_SEL = ".navbar__username, .shopee-avatar, [data-sqe='avatar'], .__username"
ERROR_SEL = ".shopee-authen__error, [class*='authen-error']"
CAPTCHA_SEL = "#captcha, .geetest_holder, iframe[src*='recaptcha']"


class ShopeeAdapter(SiteAdapter):
    session_key = "shopee_sg"  # default; overridden per-domain in __init__
    login_url = "https://shopee.sg/buyer/login"

    def __init__(self, url: str = ""):
        super().__init__(url)
        if url:
            domain = urlparse(url).netloc.lstrip("www.")
            self.session_key = f"shopee_{domain.replace('.', '_')}"
            self.login_url = f"https://{domain}/buyer/login"
            self._cookie_domain = f".{domain}"
        else:
            self._cookie_domain = ".shopee.sg"

    # ------------------------------------------------------------------

    async def login(
        self,
        username: str,
        password: str,
        proxy_url: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError:
            logger.error("Camoufox not installed.")
            return None

        logger.info("[ShopeeAdapter] Login to %s as %s", self.login_url, username)

        launch_kwargs: Dict = {"headless": "virtual", "geoip": True}
        if proxy_url:
            launch_kwargs["proxy"] = {"server": proxy_url}

        try:
            async with AsyncCamoufox(**launch_kwargs) as browser:
                page = await browser.new_page()

                await page.goto(self.login_url, timeout=30000, wait_until="networkidle")
                await page.wait_for_timeout(random.randint(800, 1500))

                # Detect CAPTCHA before attempting login
                captcha = await page.query_selector(CAPTCHA_SEL)
                if captcha:
                    logger.error("[ShopeeAdapter] CAPTCHA detected — cannot auto-login")
                    return None

                await page.wait_for_selector(USERNAME_SEL, timeout=10000)
                await page.fill(USERNAME_SEL, username)
                await page.wait_for_timeout(random.randint(300, 600))

                await page.fill(PASSWORD_SEL, password)
                await page.wait_for_timeout(random.randint(300, 600))

                await page.click(SUBMIT_SEL)

                # Wait for success or error
                try:
                    await page.wait_for_selector(
                        f"{SUCCESS_SEL}, {ERROR_SEL}, {CAPTCHA_SEL}",
                        timeout=15000,
                    )
                except Exception:
                    logger.error("[ShopeeAdapter] Timed out waiting for login result")
                    return None

                # Check for post-submit CAPTCHA
                if await page.query_selector(CAPTCHA_SEL):
                    logger.error("[ShopeeAdapter] CAPTCHA appeared after submit")
                    return None

                # Check for error message
                error_el = await page.query_selector(ERROR_SEL)
                if error_el:
                    msg = await error_el.inner_text()
                    logger.error("[ShopeeAdapter] Login error: %s", msg.strip())
                    return None

                await page.wait_for_timeout(random.randint(500, 1000))

                raw_cookies = await page.context.cookies()
                cookies = {c["name"]: c["value"] for c in raw_cookies}

                if not cookies:
                    logger.error("[ShopeeAdapter] No cookies extracted")
                    return None

                from app.services.session_manager import save_session
                save_session(self.session_key, cookies)

                logger.info(
                    "[ShopeeAdapter] Login OK — %d cookies saved (key=%s)",
                    len(cookies), self.session_key,
                )
                return cookies

        except Exception as exc:
            logger.error("[ShopeeAdapter] Unexpected error: %s", exc)
            return None

    # ------------------------------------------------------------------

    def is_login_gate(self, html: str) -> bool:
        lower = html.lower()
        return any(indicator in lower for indicator in LOGIN_GATE_INDICATORS)
