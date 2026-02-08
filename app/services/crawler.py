import itertools
import logging
import random
import time
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse

import httpx
import redis

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


def build_headers(url: str, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
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


def auth_line_to_proxy_url(line: str) -> Optional[str]:
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
    else:
        logger.warning(f"Unsupported proxy format: {line}")
        return None


class ProxyRateLimiter:
    KEY_PREFIX = "proxy_cd:"

    def __init__(self, redis_url: str, per_proxy_delay: float):
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._delay_ms = int(per_proxy_delay * 1000)

    def try_acquire(self, proxy: str) -> bool:
        key = f"{self.KEY_PREFIX}{proxy}"
        return self._redis.set(key, "1", nx=True, px=self._delay_ms) is not None

    def wait_and_acquire(self, proxies: List[str], timeout: float = 60) -> Optional[str]:
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
    def __init__(self, proxy_list: List[str], per_proxy_delay: float = 5.0,
                 redis_url: Optional[str] = None):
        self.proxies = proxy_list
        self.per_proxy_delay = per_proxy_delay

        self.bad_proxies: set[str] = set()
        self.blocked_proxies: set[str] = set()
        self.proxy_total_requests: Dict[str, int] = {}
        self.proxy_successful_requests: Dict[str, int] = {}
        self.proxy_success_rate: Dict[str, float] = {}

        self.max_requests_per_proxy = 15
        self.min_success_rate = 0.3
        self.total_requests = 0

        self.rate_limiter = ProxyRateLimiter(
            redis_url=redis_url or settings.redis_url,
            per_proxy_delay=per_proxy_delay,
        )

        logger.info(f"Loaded {len(proxy_list)} proxies, per-proxy delay: {per_proxy_delay}s")

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
                logger.warning(f"Proxy {proxy} low success rate")
                self.bad_proxies.add(proxy)
                return False

        return True

    def get_healthy_proxies(self) -> List[str]:
        return [p for p in self.proxies if self._is_healthy(p)]

    def pick_proxy_line(self, timeout: float = 60) -> Optional[str]:
        healthy = self.get_healthy_proxies()
        if not healthy:
            logger.error("No healthy proxies available")
            return None

        self.total_requests += 1

        healthy.sort(
            key=lambda p: self.proxy_success_rate.get(p, 1.0),
            reverse=True,
        )

        for proxy in healthy:
            if self.rate_limiter.try_acquire(proxy):
                logger.debug(f"Acquired proxy: {proxy}")
                return proxy

        logger.info("All proxies on cooldown, waiting...")
        proxy = self.rate_limiter.wait_and_acquire(healthy, timeout=timeout)
        if proxy:
            logger.debug(f"Acquired proxy after wait: {proxy}")
        return proxy

    def report_request_result(self, proxy: str, success: bool, blocked: bool = False):
        if blocked:
            self.blocked_proxies.add(proxy)
            logger.error(f"Proxy blocked: {proxy}")
        elif not success:
            self.bad_proxies.add(proxy)
            logger.warning(f"Proxy marked bad: {proxy}")

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

    def get_stats(self) -> Dict[str, Any]:
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
            proxy_file: Optional[str] = None,
            max_retries: int = 3,
            timeout: float = 15.0,
            delay: float = 1.0,
            headers: Optional[Dict[str, str]] = None,
            use_http2: bool = True,
            ban_indicators: Optional[List[str]] = None,
            min_content_length: int = 500,
    ):
        self.proxy_file = proxy_file
        self.max_retries = max_retries
        self.timeout = timeout
        self.delay = delay
        self.extra_headers = headers
        self.use_http2 = use_http2
        self.ban_indicators = ban_indicators or GENERIC_BAN_INDICATORS
        self.min_content_length = min_content_length

        proxies: list[str] = []
        if proxy_file:
            try:
                with open(proxy_file, "r") as f:
                    proxies = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
                logger.info(f"Loaded {len(proxies)} proxies from {proxy_file}")
            except FileNotFoundError:
                logger.error(f"Proxy file not found: {proxy_file}")

        self.proxy_pool = SmartProxyPool(
            proxy_list=proxies,
            per_proxy_delay=delay,
        ) if proxies else None

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

    def _build_client(self, proxy_url: Optional[str] = None) -> httpx.Client:
        kwargs = {
            "http2": self.use_http2,
            "timeout": httpx.Timeout(connect=10, read=self.timeout, write=10, pool=5),
            "follow_redirects": True,
        }
        if proxy_url:
            kwargs["proxy"] = proxy_url
        return httpx.Client(**kwargs)

    def _do_request(self, url: str, proxy_line: Optional[str] = None) -> Optional[bytes]:
        proxy_url = auth_line_to_proxy_url(proxy_line) if proxy_line else None
        request_headers = build_headers(url, self.extra_headers)

        with self._build_client(proxy_url) as client:
            res = client.get(url, headers=request_headers)
            self._request_count += 1

            if 200 <= res.status_code < 300:
                content = res.content.decode("utf-8", "replace")
                if self.is_blocked_response(content):
                    raise BlockedError(f"Blocked response from {url}")
                self._successful_requests += 1
                return res.content

            elif res.status_code == 404:
                logger.warning(f"404 Not Found: {url}")
                return None

            elif res.status_code in (403, 429, 503):
                raise BlockedError(f"HTTP {res.status_code} from {url}")

            else:
                raise httpx.HTTPStatusError(
                    f"HTTP {res.status_code}",
                    request=res.request,
                    response=res,
                )

    def crawl_bytes(self, url: str) -> Optional[bytes]:
        if self.proxy_pool:
            return self._crawl_with_proxies(url)
        return self._crawl_direct(url)

    def _crawl_direct(self, url: str) -> Optional[bytes]:
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(f"Crawling {url} (direct, attempt {attempt})")
                return self._do_request(url)
            except BlockedError as e:
                logger.warning(f"Blocked: {e}")
                self._blocked_requests += 1
                time.sleep(self.delay * attempt * 3)
            except Exception as e:
                logger.error(f"Error: {str(e)[:100]}")
                self._failed_requests += 1
                time.sleep(self.delay * attempt)
        return None

    def _crawl_with_proxies(self, url: str) -> Optional[bytes]:
        for attempt in range(1, self.max_retries + 1):
            proxy_line = self.proxy_pool.pick_proxy_line()
            if not proxy_line:
                logger.error("No proxies available")
                break

            try:
                logger.info(f"Crawling {url} via proxy (attempt {attempt})")
                result = self._do_request(url, proxy_line)
                self.proxy_pool.report_request_result(proxy_line, True)
                return result

            except BlockedError as e:
                logger.warning(f"Blocked via {proxy_line}: {e}")
                self.proxy_pool.report_request_result(proxy_line, False, blocked=True)
                self._blocked_requests += 1

            except Exception as e:
                logger.error(f"Error with {proxy_line}: {str(e)[:100]}")
                self.proxy_pool.report_request_result(proxy_line, False)
                self._failed_requests += 1

        return None

    def crawl(self, url: str) -> Optional[str]:
        data = self.crawl_bytes(url)
        return data.decode("utf-8", "replace") if data else None

    def get_stats(self) -> Dict[str, Any]:
        total = self._request_count or 1
        return {
            "total_requests": self._request_count,
            "successful_requests": self._successful_requests,
            "blocked_requests": self._blocked_requests,
            "failed_requests": self._failed_requests,
            "success_rate": self._successful_requests / total,
            "proxy_stats": self.proxy_pool.get_stats() if self.proxy_pool else {},
        }


class BlockedError(Exception):
    pass
