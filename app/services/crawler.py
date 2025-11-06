import hashlib
import itertools
import logging
import random
import time
from typing import Optional, Dict, Any
import httpx

HEADERS_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
]

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.9,uk;q=0.8",
    "Connection": "keep-alive",
    "Host": "djinni.co",
    "User-Agent": random.choice(HEADERS_POOL),
}


def auth_line_to_proxy_url(line: str) -> Optional[str]:
    s = line.strip()
    if not s:
        return None
    if "://" in s:
        s = s.split("://", 1)[1]
    parts = s.split(":")

    if len(parts) == 2:
        # ip:port
        host, port = parts
        return f"http://{host}:{port}"
    elif len(parts) == 4:
        # ip:port:user:pass
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
        self.headers = headers or DEFAULT_HEADERS
        self.use_http2 = use_http2

        proxies: list[str] = []
        if proxy_file:
            try:
                with open(proxy_file, "r") as f:
                    proxies = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
            except FileNotFoundError:
                proxies = []
        self._proxies = proxies
        self._proxy_cycle = itertools.cycle(proxies) if proxies else None
        self._bad_proxies: set[str] = set()

        self._request_count = 0
        self._current_proxy = None

    import time
    import hashlib

    def pick_proxy_line(self) -> Optional[str]:
        if not self._proxies:
            return None

        available_proxies = [p for p in self._proxies if p not in self._bad_proxies]
        if not available_proxies:
            return None

        time_seed = int(time.time() / 10)
        hash_seed = hashlib.md5(str(time_seed).encode()).hexdigest()
        proxy_index = int(hash_seed[:8], 16) % len(available_proxies)

        return available_proxies[proxy_index]

    def crawl_bytes(self, url: str) -> Optional[bytes]:
        tries = 0
        last_exc: Optional[Exception] = None
        while tries < self.max_retries:
            proxy_line = self.pick_proxy_line()
            proxy_url = to_httpx_proxy(proxy_line)
            try:
                print(f"Crawling {url} with proxy {proxy_url} UA: {self.headers.get('User-Agent')}")
                timeout = httpx.Timeout(connect=6, read=self.timeout, write=10, pool=5)
                with httpx.Client(
                        http2=self.use_http2,
                        proxy=proxy_url,
                        timeout=timeout,
                        follow_redirects=True,
                        headers=self.headers,
                ) as client:
                    res = client.get(url)
                    self._request_count += 1
                    if 200 <= res.status_code < 300:
                        return res.content
                    else:
                        if proxy_url:
                            self._bad_proxies.add(proxy_line or "")
            except Exception as e:
                last_exc = e
                if proxy_url:
                    self._bad_proxies.add(proxy_line or "")
            tries += 1
            time.sleep(self.delay)
        if last_exc:
            print("Failed:", last_exc)
        return None

    def crawl(self, url: str) -> Optional[str]:
        data = self.crawl_bytes(url)
        if data is None:
            return None
        return data.decode("utf-8", "replace")
