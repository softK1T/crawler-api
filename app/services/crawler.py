import logging
import random
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import redis
from bs4 import BeautifulSoup

from app.core.config import settings

HEADERS_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:134.0) Gecko/20100101 Firefox/134.0",
]

GENERIC_BAN_INDICATORS = [
    "access denied",
    "forbidden",
    "has been blocked",
    "blocked",
    "captcha",
    "cf-challenge",
    "enable javascript",
    "unusual traffic",
    "rate limit",
    "too many requests",
    "please verify",
]

logger = logging.getLogger(__name__)

# Type alias: (body_bytes, status_code, content_type, response_headers)
CrawlRaw = tuple[bytes, int, str, dict[str, str]]


def build_headers(url: str, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
    parsed = urlparse(url)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "en-US,en;q=0.9,pl;q=0.8,uk;q=0.7",
        "Connection": "keep-alive",
        "Host": parsed.netloc,
        "User-Agent": random.choice(HEADERS_POOL),
        "DNT": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if extra_headers:
        headers.update(extra_headers)
    return headers


def html_to_markdown(html: str) -> str:
    """Convert HTML to clean Markdown."""
    try:
        import html2text

        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.body_width = 0
        return h.handle(html)
    except ImportError:
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(separator="\n", strip=True)


def extract_with_selectors(html: str, selectors: dict[str, str]) -> dict[str, Any]:
    """Extract data from HTML using CSS selectors map."""
    soup = BeautifulSoup(html, "html.parser")
    result: dict[str, Any] = {}
    for field, selector in selectors.items():
        elements = soup.select(selector)
        if not elements:
            result[field] = None
        elif len(elements) == 1:
            result[field] = elements[0].get_text(strip=True)
        else:
            result[field] = [el.get_text(strip=True) for el in elements]
    return result


def auth_line_to_proxy_url(line: str) -> str | None:
    """
    Parse a proxy line into an httpx-compatible URL.
    Supported formats:
      host:port
      host:port:user:pass
      host:port:user:pass:COUNTRY   (5-part geo-tagged format, country stripped here)
      http://user:pass@host:port    (full URL, auto-stripped)
    """
    s = line.strip()
    if not s:
        return None
    if "://" in s:
        s = s.split("://", 1)[1]
    parts = s.split(":")

    if len(parts) == 2:
        host, port = parts
        return f"http://{host}:{port}"
    elif len(parts) == 4:
        host, port, user, pwd = parts
        return f"http://{user}:{pwd}@{host}:{port}"
    elif len(parts) == 5:
        # host:port:user:pass:COUNTRY — strip country tag
        host, port, user, pwd, _country = parts
        return f"http://{user}:{pwd}@{host}:{port}"
    else:
        logger.warning("Unsupported proxy format: %s", line)
        return None


class ProxyRateLimiter:
    KEY_PREFIX = "proxy_cd:"

    def __init__(self, redis_url: str, per_proxy_delay: float):
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._delay_ms = int(per_proxy_delay * 1000)

    def try_acquire(self, proxy: str) -> bool:
        key = f"{self.KEY_PREFIX}{proxy}"
        return self._redis.set(key, "1", nx=True, px=self._delay_ms) is not None

    def wait_and_acquire(self, proxies: list[str], timeout: float = 60) -> str | None:
        deadline = time.time() + timeout
        candidates = list(proxies)
        while time.time() < deadline:
            random.shuffle(candidates)
            for proxy in candidates:
                if self.try_acquire(proxy):
                    return proxy
            time.sleep(0.3)
        return None

    def ttl_remaining(self, proxy: str) -> float:
        ttl_ms = self._redis.pttl(f"{self.KEY_PREFIX}{proxy}")
        return max(0.0, ttl_ms / 1000) if ttl_ms and ttl_ms > 0 else 0.0


class SmartProxyPool:
    def __init__(
        self, proxy_list: list[str], per_proxy_delay: float = 5.0, redis_url: str | None = None
    ):
        self.proxies = proxy_list
        self.per_proxy_delay = per_proxy_delay

        self.bad_proxies: set = set()
        self.blocked_proxies: set = set()
        self.proxy_total_requests: dict[str, int] = {}
        self.proxy_successful_requests: dict[str, int] = {}
        self.proxy_success_rate: dict[str, float] = {}

        self.max_requests_per_proxy = 15
        self.min_success_rate = 0.3
        self.total_requests = 0

        self.rate_limiter = ProxyRateLimiter(
            redis_url=redis_url or settings.redis_url,
            per_proxy_delay=per_proxy_delay,
        )

        logger.info("Loaded %d proxies, per-proxy delay: %ss", len(proxy_list), per_proxy_delay)

    def _update_proxy_stats(self, proxy: str, success: bool):
        if proxy not in self.proxy_total_requests:
            self.proxy_total_requests[proxy] = 0
            self.proxy_successful_requests[proxy] = 0
        self.proxy_total_requests[proxy] += 1
        if success:
            self.proxy_successful_requests[proxy] += 1
        total = self.proxy_total_requests[proxy]
        successful = self.proxy_successful_requests[proxy]
        self.proxy_success_rate[proxy] = successful / total if total > 0 else 0.0

    def _is_healthy(self, proxy: str) -> bool:
        if proxy in self.bad_proxies or proxy in self.blocked_proxies:
            return False
        if self.proxy_total_requests.get(proxy, 0) >= 5:
            if self.proxy_success_rate.get(proxy, 1.0) < self.min_success_rate:
                logger.warning("Proxy %s low success rate — marking bad", proxy)
                self.bad_proxies.add(proxy)
                return False
        return True

    def get_healthy_proxies(self) -> list[str]:
        return [p for p in self.proxies if self._is_healthy(p)]

    def pick_proxy_line(self, timeout: float = 60) -> str | None:
        healthy = self.get_healthy_proxies()
        if not healthy:
            logger.error("No healthy proxies available")
            return None
        self.total_requests += 1
        healthy.sort(key=lambda p: self.proxy_success_rate.get(p, 1.0), reverse=True)
        for proxy in healthy:
            if self.rate_limiter.try_acquire(proxy):
                logger.debug("Acquired proxy: %s", proxy)
                return proxy
        logger.info("All proxies on cooldown, waiting...")
        return self.rate_limiter.wait_and_acquire(healthy, timeout=timeout)

    def report_request_result(self, proxy: str, success: bool, blocked: bool = False):
        if blocked:
            self.blocked_proxies.add(proxy)
            logger.error("Proxy blocked: %s", proxy)
        elif not success:
            self.bad_proxies.add(proxy)
            logger.warning("Proxy marked bad: %s", proxy)
        self._update_proxy_stats(proxy, success and not blocked)

    def reset_proxy(self, proxy: str):
        self.bad_proxies.discard(proxy)
        self.blocked_proxies.discard(proxy)
        self.proxy_total_requests[proxy] = 0
        self.proxy_successful_requests[proxy] = 0
        self.proxy_success_rate[proxy] = 0.0

    def reset_all(self):
        for proxy in self.proxies:
            self.reset_proxy(proxy)

    def get_stats(self) -> dict[str, Any]:
        healthy = len(self.get_healthy_proxies())
        return {
            "total_proxies": len(self.proxies),
            "healthy": healthy,
            "blocked": len(self.blocked_proxies),
            "bad": len(self.bad_proxies),
            "total_requests": self.total_requests,
        }


class Crawler:
    def __init__(
        self,
        proxy_pool=None,  # accepts GeoProxyPool / SmartProxyPool singleton
        proxy_file: str | None = None,  # legacy: load from file if no pool given
        max_retries: int = 3,
        timeout: float = 15.0,
        delay: float = 1.0,
        headers: dict[str, str] | None = None,
        use_http2: bool = True,
        ban_indicators: list[str] | None = None,
        min_content_length: int = 500,
        proxy_country: str | None = None,
    ):
        self.max_retries = max_retries
        self.timeout = timeout
        self.delay = delay
        self.extra_headers = headers
        self.use_http2 = use_http2
        self.ban_indicators = ban_indicators or GENERIC_BAN_INDICATORS
        self.min_content_length = min_content_length
        self.proxy_country = proxy_country

        if proxy_pool is not None:
            # Use provided singleton pool (preferred)
            self.proxy_pool = proxy_pool
        elif proxy_file:
            # Legacy: build pool from file (stats lost between tasks)
            try:
                with open(proxy_file) as f:
                    proxies = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
                from app.services.geo_proxy_pool import GeoProxyPool

                self.proxy_pool = GeoProxyPool(proxy_list=proxies, per_proxy_delay=delay)
                logger.info("Loaded %d proxies from %s (legacy mode)", len(proxies), proxy_file)
            except FileNotFoundError:
                logger.error("Proxy file not found: %s", proxy_file)
                self.proxy_pool = None
        else:
            self.proxy_pool = None

        self._request_count = 0
        self._successful_requests = 0
        self._blocked_requests = 0
        self._failed_requests = 0

    def is_blocked_response(self, content: str) -> bool:
        if not content or len(content) < self.min_content_length:
            return True
        if len(content) > 50_000:
            return False
        content_lower = content.lower()
        return any(indicator in content_lower for indicator in self.ban_indicators)

    def _build_client(self, proxy_url: str | None = None) -> httpx.Client:
        kwargs: dict[str, Any] = {
            "http2": self.use_http2,
            "timeout": httpx.Timeout(connect=10, read=self.timeout, write=10, pool=5),
            # Redirects are followed manually so every hop passes url_guard.
            "follow_redirects": False,
        }
        if proxy_url:
            kwargs["proxy"] = proxy_url
        return httpx.Client(**kwargs)

    def _get_guarded(
        self, client: httpx.Client, url: str, headers: dict[str, str]
    ) -> httpx.Response:
        """GET with per-hop URL validation and a hard body-size cap."""
        from app.core.url_guard import (
            MAX_BODY_BYTES,
            MAX_REDIRECTS,
            BodyTooLarge,
            UrlNotAllowed,
            validate_url_sync,
        )

        current = url
        for hop in range(MAX_REDIRECTS + 1):
            validate_url_sync(current)
            with client.stream("GET", current, headers=headers) as res:
                if res.is_redirect:
                    location = res.headers.get("location")
                    if not location:
                        raise UrlNotAllowed(f"Redirect from {current} without Location header.")
                    current = str(res.url.join(location))
                    continue

                declared = res.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
                    raise BodyTooLarge(f"Content-Length {declared} exceeds {MAX_BODY_BYTES}.")

                chunks: list[bytes] = []
                total = 0
                for chunk in res.iter_bytes():
                    total += len(chunk)
                    if total > MAX_BODY_BYTES:
                        raise BodyTooLarge(f"Body exceeded {MAX_BODY_BYTES} bytes while streaming.")
                    chunks.append(chunk)
                res._content = b"".join(chunks)
                return res

        raise UrlNotAllowed(f"Exceeded {MAX_REDIRECTS} redirects starting from {url}.")

    def _do_request(self, url: str, proxy_line: str | None = None) -> CrawlRaw | None:
        proxy_url = auth_line_to_proxy_url(proxy_line) if proxy_line else None
        request_headers = build_headers(url, self.extra_headers)

        with self._build_client(proxy_url) as client:
            res = self._get_guarded(client, url, request_headers)
            self._request_count += 1

            content_type = res.headers.get("content-type", "")
            headers_trunc = dict(list(res.headers.items())[:20])

            if 200 <= res.status_code < 300:
                content = res.content.decode("utf-8", "replace")
                if self.is_blocked_response(content):
                    raise BlockedError(f"Blocked response from {url}")
                self._successful_requests += 1
                return res.content, res.status_code, content_type, headers_trunc
            elif res.status_code == 404:
                logger.warning("404 Not Found: %s", url)
                return None
            elif res.status_code in (403, 429, 503):
                raise BlockedError(f"HTTP {res.status_code} from {url}")
            else:
                raise httpx.HTTPStatusError(
                    f"HTTP {res.status_code}",
                    request=res.request,
                    response=res,
                )

    def crawl_raw(self, url: str) -> CrawlRaw | None:
        if self.proxy_pool:
            return self._crawl_with_proxies(url)
        return self._crawl_direct(url)

    def crawl_bytes(self, url: str) -> bytes | None:
        result = self.crawl_raw(url)
        return result[0] if result else None

    def _crawl_direct(self, url: str) -> CrawlRaw | None:
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info("Crawling %s (direct, attempt %d)", url, attempt)
                return self._do_request(url)
            except BlockedError as e:
                logger.warning("Blocked: %s", e)
                self._blocked_requests += 1
                time.sleep(self.delay * attempt * 3)
            except Exception as e:
                logger.error("Error: %s", str(e)[:100])
                self._failed_requests += 1
                time.sleep(self.delay * attempt)
        return None

    def _pick_proxy(self) -> str | None:
        """Pick proxy — geo-aware if pool supports it and country is set."""
        from app.services.geo_proxy_pool import GeoProxyPool

        if isinstance(self.proxy_pool, GeoProxyPool) and self.proxy_country:
            return self.proxy_pool.pick_proxy_for_country(self.proxy_country)
        return self.proxy_pool.pick_proxy_line()

    def _crawl_with_proxies(self, url: str) -> CrawlRaw | None:
        for attempt in range(1, self.max_retries + 1):
            proxy_line = self._pick_proxy()
            if not proxy_line:
                logger.error("No proxies available")
                break
            try:
                logger.info(
                    "Crawling %s via proxy country=%s (attempt %d)",
                    url,
                    self.proxy_country,
                    attempt,
                )
                result = self._do_request(url, proxy_line)
                self.proxy_pool.report_request_result(proxy_line, True)
                return result
            except BlockedError as e:
                logger.warning("Blocked via %s: %s", proxy_line, e)
                self.proxy_pool.report_request_result(proxy_line, False, blocked=True)
                self._blocked_requests += 1
            except Exception as e:
                logger.error("Error with %s: %s", proxy_line, str(e)[:100])
                self.proxy_pool.report_request_result(proxy_line, False)
                self._failed_requests += 1
        return None

    def crawl(self, url: str) -> str | None:
        data = self.crawl_bytes(url)
        return data.decode("utf-8", "replace") if data else None

    def get_stats(self) -> dict[str, Any]:
        total = self._request_count or 1
        return {
            "total_requests": self._request_count,
            "successful_requests": self._successful_requests,
            "blocked_requests": self._blocked_requests,
            "failed_requests": self._failed_requests,
            "success_rate": round(self._successful_requests / total, 3),
            "proxy_stats": self.proxy_pool.get_stats() if self.proxy_pool else {},
        }


async def crawl_browser(
    url: str, timeout: int = 15, wait_for: str | None = None
) -> CrawlRaw | None:
    """
    Browser-based crawl using Playwright (handles JS-rendered pages).
    Requires: pip install playwright && playwright install chromium
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error(
            "Playwright not installed. Run: pip install playwright && playwright install chromium"
        )
        return None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=random.choice(HEADERS_POOL),
                locale="en-US",
            )
            page = await context.new_page()
            response = await page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
            if wait_for:
                await page.wait_for_selector(wait_for, timeout=5000)
            html = await page.content()
            status_code = response.status if response else 200
            await browser.close()
            return html.encode("utf-8"), status_code, "text/html", {}
    except Exception as exc:
        logger.error("Browser crawl failed for %s: %s", url, exc)
        return None


class BlockedError(Exception):
    pass
