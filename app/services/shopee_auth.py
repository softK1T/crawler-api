import logging
import random
from typing import Optional, Dict

logger = logging.getLogger(__name__)

SHOPEE_LOGIN_URL = "https://shopee.sg/buyer/login"
SHOPEE_SESSION_KEY = "shopee_sg"

# Selectors for Shopee login form
USERNAME_SELECTOR = 'input[name="loginKey"]'
PASSWORD_SELECTOR = 'input[name="password"]'
SUBMIT_SELECTOR = 'button[type="submit"]'
LOGIN_SUCCESS_SELECTOR = ".navbar__username, .shopee-avatar, [data-sqe=\"avatar\"]"  # visible after login


async def shopee_login(
    username: str,
    password: str,
    proxy_url: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """
    Performs Shopee login via camoufox browser, returns cookies dict.
    Saves cookies to Redis via session_manager.
    """
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        logger.error("Camoufox not installed.")
        return None

    logger.info("[shopee_auth] Starting login for user: %s", username)

    try:
        launch_kwargs: Dict = {
            "headless": "virtual",
            "geoip": True,
        }
        if proxy_url:
            launch_kwargs["proxy"] = {"server": proxy_url}

        async with AsyncCamoufox(**launch_kwargs) as browser:
            page = await browser.new_page()

            # 1. Open login page
            await page.goto(SHOPEE_LOGIN_URL, timeout=30000, wait_until="networkidle")
            await page.wait_for_timeout(random.randint(1000, 2000))

            # 2. Fill username
            await page.wait_for_selector(USERNAME_SELECTOR, timeout=10000)
            await page.fill(USERNAME_SELECTOR, username)
            await page.wait_for_timeout(random.randint(300, 700))

            # 3. Fill password
            await page.fill(PASSWORD_SELECTOR, password)
            await page.wait_for_timeout(random.randint(300, 700))

            # 4. Click submit
            await page.click(SUBMIT_SELECTOR)

            # 5. Wait for successful login (avatar/username appears)
            try:
                await page.wait_for_selector(LOGIN_SUCCESS_SELECTOR, timeout=15000)
                logger.info("[shopee_auth] Login successful")
            except Exception:
                # Try to detect error message
                error_el = await page.query_selector(".shopee-authen__error, [class*=error]")
                if error_el:
                    error_text = await error_el.inner_text()
                    logger.error("[shopee_auth] Login failed: %s", error_text)
                else:
                    logger.error("[shopee_auth] Login timed out waiting for success selector")
                return None

            await page.wait_for_timeout(random.randint(500, 1000))

            # 6. Extract cookies from browser context
            context = page.context
            raw_cookies = await context.cookies()

            cookies = {c["name"]: c["value"] for c in raw_cookies}
            logger.info("[shopee_auth] Extracted %d cookies", len(cookies))

            # 7. Save to Redis
            from app.services.session_manager import save_session, SHOPEE_SESSION_KEY
            save_session(SHOPEE_SESSION_KEY, cookies)

            return cookies

    except Exception as exc:
        logger.error("[shopee_auth] Error during login: %s", exc)
        return None
