import logging
import random
import time
from typing import Optional, Dict, Any, Tuple, List

from app.services.crawler import (
    HEADERS_POOL,
    GENERIC_BAN_INDICATORS,
    build_headers,
    html_to_markdown,
    extract_with_selectors,
    auth_line_to_proxy_url,
    CrawlRaw,
    BlockedError,
)

logger = logging.getLogger(__name__)

# Latest Chrome impersonate targets from curl_cffi
# Use 'chrome' alias to always use the latest available version
IMPERSONATE_TARGETS = [
    "chrome",
    "chrome146",
    "chrome131",
    "chrome124",
]


class StealthCrawler:
    """
    Tier-2 crawler using curl_cffi to impersonate real Chrome TLS fingerprints.
    Bypasses:
      - JA3 / JA3N TLS fingerprinting
      - HTTP/2 ALPN fingerprinting
      - Cloudflare Bot Management (basic/medium protection levels)
      - Akamai Bot Manager (basic level)

    Does NOT bypass:
      - Cloudflare JS challenge (use camoufox for that)
      - CAPTCHA (use camoufox)
      - Device fingerprinting / Canvas / WebGL
    """

    def __init__(
        self,
        proxy_pool=None,
        max_retries: int = 3,
        timeout: float = 20.0,
        delay: float = 2.0,
        headers: Optional[Dict[str, str]] = None,
        proxy_country: Optional[str] = None,
        ban_indicators: Optional[List[str]] = None,
        min_content_length: int = 500,
    ):
        self.proxy_pool = proxy_pool
        self.max_retries = max_retries
        self.timeout = timeout
        self.delay = delay
        self.extra_headers = headers or {}
        self.proxy_country = proxy_country
        self.ban_indicators = ban_indicators or GENERIC_BAN_INDICATORS
        self.min_content_length = min_content_length

    def _pick_proxy_url(self) -> Optional[str]:
        if not self.proxy_pool:
            return None
        from app.services.geo_proxy_pool import GeoProxyPool
        if isinstance(self.proxy_pool, GeoProxyPool) and self.proxy_country:
            proxy_line = self.proxy_pool.pick_proxy_for_country(self.proxy_country)
        else:
            proxy_line = self.proxy_pool.pick_proxy_line()
        return auth_line_to_proxy_url(proxy_line) if proxy_line else None

    def is_blocked(self, content: str) -> bool:
        if not content or len(content) < self.min_content_length:
            return True
        content_lower = content.lower()
        return any(ind in content_lower for ind in self.ban_indicators)

    def crawl_raw(self, url: str) -> Optional[CrawlRaw]:
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            logger.error("curl_cffi not installed. Run: pip install curl-cffi")
            return None

        for attempt in range(1, self.max_retries + 1):
            impersonate = random.choice(IMPERSONATE_TARGETS)
            proxy_url = self._pick_proxy_url()

            try:
                logger.info(
                    "[stealth] Crawling %s (attempt %d, impersonate=%s, proxy=%s)",
                    url, attempt, impersonate, proxy_url or "direct"
                )

                kwargs: Dict[str, Any] = {
                    "impersonate": impersonate,
                    "timeout": self.timeout,
                    "allow_redirects": True,
                    "verify": True,
                }
                if self.extra_headers:
                    kwargs["headers"] = self.extra_headers
                if proxy_url:
                    kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}

                resp = cffi_requests.get(url, **kwargs)

                content_type = resp.headers.get("content-type", "")
                resp_headers = dict(list(resp.headers.items())[:20])

                if resp.status_code in (403, 429, 503):
                    raise BlockedError(f"HTTP {resp.status_code} — likely bot detection")

                if 200 <= resp.status_code < 300:
                    html = resp.text
                    if self.is_blocked(html):
                        raise BlockedError("Blocked response content (CAPTCHA or challenge page)")
                    logger.info("[stealth] Success %s (HTTP %d)", url, resp.status_code)
                    if self.proxy_pool and proxy_url:
                        proxy_line = proxy_url  # approximate
                        self.proxy_pool.report_request_result(proxy_line, True)
                    return resp.content, resp.status_code, content_type, resp_headers
                else:
                    logger.warning("[stealth] HTTP %d for %s", resp.status_code, url)

            except BlockedError as e:
                logger.warning("[stealth] Blocked attempt %d: %s", attempt, e)
                time.sleep(self.delay * attempt * 2)
            except Exception as e:
                logger.error("[stealth] Error attempt %d: %s", attempt, str(e)[:120])
                time.sleep(self.delay * attempt)

        return None


async def crawl_camoufox(
    url: str,
    timeout: int = 30,
    proxy_url: Optional[str] = None,
    wait_for: Optional[str] = None,
    locale: str = "en-US",
) -> Optional[CrawlRaw]:
    """
    Tier-3 crawler using Camoufox anti-detect Firefox browser.
    Bypasses:
      - Cloudflare JS challenge (Turnstile, IUAM)
      - Device fingerprinting (Canvas, WebGL, AudioContext, fonts)
      - navigator.webdriver detection
      - Headless browser detection
      - Shopee, Ticketmaster, major e-commerce anti-bot

    Requires:
      pip install camoufox[geoip]
      python -m camoufox fetch
    """
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        logger.error(
            "Camoufox not installed. Run: pip install 'camoufox[geoip]' && python -m camoufox fetch"
        )
        return None

    try:
        launch_kwargs: Dict[str, Any] = {
            "headless": True,
            "geoip": True,  # auto-spoof timezone/locale from proxy IP
        }
        if proxy_url:
            launch_kwargs["proxy"] = {"server": proxy_url}
        if locale:
            launch_kwargs["locale"] = locale

        async with AsyncCamoufox(**launch_kwargs) as browser:
            page = await browser.new_page()

            # Random human-like viewport
            await page.set_viewport_size({
                "width": random.choice([1366, 1440, 1920, 2560]),
                "height": random.choice([768, 900, 1080]),
            })

            response = await page.goto(
                url,
                timeout=timeout * 1000,
                wait_until="networkidle",
            )

            # Wait for specific element if requested
            if wait_for:
                try:
                    await page.wait_for_selector(wait_for, timeout=10000)
                except Exception:
                    logger.warning("[camoufox] wait_for selector '%s' not found", wait_for)

            # Simulate brief human reading pause
            await page.wait_for_timeout(random.randint(800, 2000))

            html = await page.content()
            status_code = response.status if response else 200
            content_type = "text/html"

            logger.info("[camoufox] Success %s (HTTP %d)", url, status_code)
            return html.encode("utf-8"), status_code, content_type, {}

    except Exception as exc:
        logger.error("[camoufox] Failed for %s: %s", url, exc)
        return None


async def crawl_playwright_stealth(
    url: str,
    timeout: int = 20,
    proxy_url: Optional[str] = None,
    wait_for: Optional[str] = None,
) -> Optional[CrawlRaw]:
    """
    Upgraded 'browser' mode using Playwright + stealth patches.
    Better than vanilla Playwright but weaker than Camoufox.
    Use as fallback when Camoufox is not installed.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return None

    try:
        async with async_playwright() as p:
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
            launch_kwargs: Dict[str, Any] = {"headless": True, "args": launch_args}
            if proxy_url:
                launch_kwargs["proxy"] = {"server": proxy_url}

            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                user_agent=random.choice(HEADERS_POOL),
                viewport={"width": random.choice([1366, 1440, 1920]), "height": random.choice([768, 900, 1080])},
                locale="en-US",
                timezone_id="America/New_York",
                java_script_enabled=True,
                ignore_https_errors=False,
            )

            # Core stealth patches
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'permissions', {
                    query: (parameters) => (
                        parameters.name === 'notifications'
                            ? Promise.resolve({ state: Notification.permission })
                            : Promise.resolve({ state: 'granted' })
                    )
                });
            """)

            page = await context.new_page()
            response = await page.goto(url, timeout=timeout * 1000, wait_until="networkidle")

            if wait_for:
                try:
                    await page.wait_for_selector(wait_for, timeout=8000)
                except Exception:
                    pass

            html = await page.content()
            status_code = response.status if response else 200
            await browser.close()

            logger.info("[playwright-stealth] Success %s (HTTP %d)", url, status_code)
            return html.encode("utf-8"), status_code, "text/html", {}

    except Exception as exc:
        logger.error("[playwright-stealth] Failed for %s: %s", url, exc)
        return None
