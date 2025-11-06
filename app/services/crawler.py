import hashlib
import itertools
import logging
import random
import time
from typing import Optional, Dict, Any, List
import httpx

HEADERS_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
]

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.9,uk;q=0.8",
    "Connection": "keep-alive",
    "Host": "djinni.co",
    "User-Agent": random.choice(HEADERS_POOL),
}

BAN_INDICATORS = [
    "has been blocked",
    "blocked",
    "magic@djinni.co",
    "your ip address",
    "contact us at",
    "access denied",
    "forbidden"
]


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
        logging.warning(f"Unsupported proxy format: {line}")
        return None


def to_httpx_proxy(proxy_auth_line: Optional[str]) -> Optional[str]:
    if not proxy_auth_line:
        return None
    proxy_uri = auth_line_to_proxy_url(proxy_auth_line)
    if not proxy_uri:
        return None
    if "://" not in proxy_uri:
        proxy_uri = "http://" + proxy_uri
    return proxy_uri


class SmartProxyPool:
    def __init__(self, proxy_list: List[str]):
        self.proxies = proxy_list
        self.proxy_cycle = itertools.cycle(proxy_list) if proxy_list else None

        self.bad_proxies: set[str] = set()
        self.blocked_proxies: set[str] = set()
        self.proxy_usage_count: Dict[str, int] = {}
        self.proxy_last_used: Dict[str, float] = {}
        self.proxy_success_rate: Dict[str, float] = {}
        self.proxy_total_requests: Dict[str, int] = {}
        self.proxy_successful_requests: Dict[str, int] = {}

        self.max_requests_per_proxy = 15
        self.cooldown_time = 300
        self.min_success_rate = 0.3
        self.rotation_interval = 10

        self.current_proxy: Optional[str] = None
        self.requests_with_current = 0
        self.total_requests = 0

        logging.info(f"Loaded {len(proxy_list)} proxies")

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

    def _is_proxy_available(self, proxy: str) -> bool:
        current_time = time.time()

        if proxy in self.bad_proxies or proxy in self.blocked_proxies:
            return False

        usage_count = self.proxy_usage_count.get(proxy, 0)
        if usage_count >= self.max_requests_per_proxy:
            last_used = self.proxy_last_used.get(proxy, 0)
            if current_time - last_used < self.cooldown_time:
                return False
            else:
                self.proxy_usage_count[proxy] = 0

        if self.proxy_total_requests.get(proxy, 0) >= 5:
            success_rate = self.proxy_success_rate.get(proxy, 1.0)
            if success_rate < self.min_success_rate:
                logging.warning(f"Proxy {proxy} low success rate: {success_rate:.2f}")
                self.bad_proxies.add(proxy)
                return False

        return True

    def get_available_proxies(self) -> List[str]:
        return [p for p in self.proxies if self._is_proxy_available(p)]

    def pick_proxy_line(self) -> Optional[str]:
        available_proxies = self.get_available_proxies()

        if not available_proxies:
            logging.error("No available proxies")
            return None

        self.total_requests += 1

        if (self.current_proxy and
                self.requests_with_current >= self.rotation_interval):
            logging.info(f"Forced rotation after {self.requests_with_current} requests")
            self.current_proxy = None
            self.requests_with_current = 0

        if (not self.current_proxy or
                self.current_proxy not in available_proxies):
            available_proxies.sort(
                key=lambda p: self.proxy_success_rate.get(p, 1.0),
                reverse=True
            )

            top_proxies = available_proxies[:min(3, len(available_proxies))]
            self.current_proxy = random.choice(top_proxies)
            self.requests_with_current = 0

            logging.info(f"Selected proxy: {self.current_proxy}")

        current_time = time.time()
        self.proxy_usage_count[self.current_proxy] = \
            self.proxy_usage_count.get(self.current_proxy, 0) + 1
        self.proxy_last_used[self.current_proxy] = current_time
        self.requests_with_current += 1

        return self.current_proxy

    def mark_proxy_blocked(self, proxy: str):
        if proxy:
            self.blocked_proxies.add(proxy)
            logging.error(f"Proxy {proxy} blocked by djinni")

    def mark_proxy_bad(self, proxy: str):
        if proxy:
            self.bad_proxies.add(proxy)
            logging.warning(f"Proxy {proxy} marked as bad")

    def report_request_result(self, proxy: str, success: bool, blocked: bool = False):
        if blocked:
            self.mark_proxy_blocked(proxy)
        elif not success:
            self.mark_proxy_bad(proxy)

        self._update_proxy_stats(proxy, success and not blocked)

    def get_stats(self) -> Dict[str, Any]:
        available = len(self.get_available_proxies())
        blocked = len(self.blocked_proxies)
        bad = len(self.bad_proxies)

        return {
            "total_proxies": len(self.proxies),
            "available": available,
            "blocked_by_djinni": blocked,
            "bad": bad,
            "current_proxy": self.current_proxy,
            "total_requests": self.total_requests,
            "requests_with_current": self.requests_with_current
        }


class Crawler:
    def __init__(
            self,
            proxy_file: Optional[str],
            max_retries: int = 3,
            timeout: float = 10.0,
            delay: float = 1.0,
            headers: Optional[Dict[str, str]] = None,
            use_http2: bool = True,
    ):
        self.proxy_file = proxy_file
        self.max_retries = max_retries
        self.timeout = timeout
        self.delay = delay
        self.headers = headers or DEFAULT_HEADERS.copy()
        self.use_http2 = use_http2

        proxies: list[str] = []
        if proxy_file:
            try:
                with open(proxy_file, "r") as f:
                    proxies = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
                logging.info(f"Loaded {len(proxies)} proxies from {proxy_file}")
            except FileNotFoundError:
                logging.error(f"Proxy file not found: {proxy_file}")
                proxies = []

        self.proxy_pool = SmartProxyPool(proxies) if proxies else None

        self._request_count = 0
        self._successful_requests = 0
        self._blocked_requests = 0

    def is_blocked_response(self, content: str) -> bool:
        if not content or len(content) < 100:
            return True

        content_lower = content.lower()
        return any(indicator in content_lower for indicator in BAN_INDICATORS)

    def is_valid_djinni_page(self, content: str) -> bool:
        if not content or len(content) < 1000:
            return False

        content_lower = content.lower()
        valid_indicators = ["djinni", "вакансии", "jobs", "vacancy"]
        return any(indicator in content_lower for indicator in valid_indicators)

    def crawl_bytes(self, url: str) -> Optional[bytes]:
        if not self.proxy_pool:
            logging.error("No proxy pool available")
            return None

        tries = 0
        last_exc: Optional[Exception] = None

        while tries < self.max_retries:
            proxy_line = self.proxy_pool.pick_proxy_line()
            if not proxy_line:
                logging.error("No available proxies")
                break

            proxy_url = to_httpx_proxy(proxy_line)

            current_headers = self.headers.copy()
            current_headers["User-Agent"] = random.choice(HEADERS_POOL)

            try:
                logging.info(f"Crawling {url} via {proxy_line}")

                timeout = httpx.Timeout(connect=6, read=self.timeout, write=10, pool=5)
                with httpx.Client(
                        http2=self.use_http2,
                        proxy=proxy_url,
                        timeout=timeout,
                        follow_redirects=True,
                        headers=current_headers,
                ) as client:
                    res = client.get(url)
                    self._request_count += 1

                    if 200 <= res.status_code < 300:
                        content = res.content.decode("utf-8", "replace")

                        if self.is_blocked_response(content):
                            logging.error(f"Proxy {proxy_line} blocked by djinni")
                            self.proxy_pool.report_request_result(proxy_line, False, blocked=True)
                            self._blocked_requests += 1
                            tries += 1
                            time.sleep(self.delay * 3)
                            continue

                        if not self.is_valid_djinni_page(content):
                            logging.warning(f"Invalid djinni page from {proxy_line}")
                            self.proxy_pool.report_request_result(proxy_line, False)
                            tries += 1
                            time.sleep(self.delay)
                            continue

                        self.proxy_pool.report_request_result(proxy_line, True)
                        self._successful_requests += 1

                        if self._request_count % 10 == 0:
                            stats = self.proxy_pool.get_stats()
                            logging.info(f"Stats: {self._successful_requests}/{self._request_count} success, "
                                         f"{stats['available']}/{stats['total_proxies']} proxies available, "
                                         f"{stats['blocked_by_djinni']} blocked")

                        return res.content
                    else:
                        logging.warning(f"HTTP {res.status_code} from {proxy_line}")
                        self.proxy_pool.report_request_result(proxy_line, False)

            except Exception as e:
                last_exc = e
                logging.error(f"Error with {proxy_line}: {str(e)[:100]}")
                self.proxy_pool.report_request_result(proxy_line, False)

            tries += 1
            delay_multiplier = min(tries, 3)
            time.sleep(self.delay * delay_multiplier)

        if last_exc:
            logging.error(f"All retries failed: {last_exc}")

        return None

    def crawl(self, url: str) -> Optional[str]:
        data = self.crawl_bytes(url)
        if data is None:
            return None
        return data.decode("utf-8", "replace")

    def get_stats(self) -> Dict[str, Any]:
        proxy_stats = self.proxy_pool.get_stats() if self.proxy_pool else {}

        return {
            "total_requests": self._request_count,
            "successful_requests": self._successful_requests,
            "blocked_requests": self._blocked_requests,
            "success_rate": self._successful_requests / self._request_count if self._request_count > 0 else 0,
            "proxy_stats": proxy_stats
        }
