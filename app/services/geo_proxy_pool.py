import logging
import random
import time
from typing import Any
from urllib.parse import urlparse

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

TLD_COUNTRY_MAP: dict[str, str] = {
    ".com.br": "BR",
    ".com.au": "AU",
    ".co.uk": "GB",
    ".co.jp": "JP",
    ".co.nz": "NZ",
    ".co.za": "ZA",
    ".de": "DE",
    ".fr": "FR",
    ".pl": "PL",
    ".ru": "RU",
    ".jp": "JP",
    ".ca": "CA",
    ".it": "IT",
    ".es": "ES",
    ".nl": "NL",
    ".ua": "UA",
    ".cz": "CZ",
    ".se": "SE",
    ".no": "NO",
    ".dk": "DK",
    ".fi": "FI",
    ".at": "AT",
    ".ch": "CH",
    ".be": "BE",
    ".pt": "PT",
    ".hu": "HU",
    ".ro": "RO",
    ".sk": "SK",
    ".bg": "BG",
    ".hr": "HR",
    ".mx": "MX",
    ".ar": "AR",
    ".in": "IN",
    ".cn": "CN",
    ".kr": "KR",
    ".tr": "TR",
    ".sa": "SA",
    ".ae": "AE",
    ".sg": "SG",
    ".id": "ID",
    ".th": "TH",
    ".vn": "VN",
    ".ng": "NG",
    ".za": "ZA",
}


def detect_country_from_url(url: str) -> str:
    """Auto-detect country from URL TLD. Falls back to 'US'."""
    try:
        host = urlparse(url).netloc.lower()
        host = host.split(":")[0]
        for tld, country in sorted(TLD_COUNTRY_MAP.items(), key=lambda x: -len(x[0])):
            if host.endswith(tld):
                return country
    except Exception:
        pass
    return "US"


# ── Inlined from app/services/crawler.py (deleted Stage 14) ──────────────────


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
        ttl_ms: int = self._redis.pttl(f"{self.KEY_PREFIX}{proxy}")
        return max(0.0, ttl_ms / 1000.0) if ttl_ms and ttl_ms > 0 else 0.0


class SmartProxyPool:
    """Health-scored proxy pool with rate limiting per proxy."""

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


# ── GeoProxyPool ─────────────────────────────────────────────────────────────


class GeoProxyPool(SmartProxyPool):
    """Extends SmartProxyPool with country-based proxy selection."""

    def __init__(self, proxy_list: list[str], **kwargs):
        self.geo_index: dict[str, list[str]] = {}
        clean_proxies: list[str] = []
        for line in proxy_list:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split(":")
            if len(parts) == 5:
                country = parts[4].upper()
                proxy_line = ":".join(parts[:4])
                self.geo_index.setdefault(country, []).append(proxy_line)
                clean_proxies.append(proxy_line)
            else:
                clean_proxies.append(stripped)
        super().__init__(proxy_list=clean_proxies, **kwargs)
        logger.info(
            "GeoProxyPool initialised: %d proxies, geo-index: %s",
            len(clean_proxies),
            {k: len(v) for k, v in self.geo_index.items()},
        )

    def pick_proxy_for_country(self, country: str, timeout: float = 60) -> str | None:
        country = country.upper()
        candidates = self.geo_index.get(country, [])
        healthy = [p for p in candidates if self._is_healthy(p)]
        if not healthy:
            logger.debug("No healthy proxies for country=%s, falling back to global pool", country)
            return self.pick_proxy_line(timeout=timeout)
        healthy.sort(key=lambda p: self.proxy_success_rate.get(p, 1.0), reverse=True)
        for proxy in healthy:
            if self.rate_limiter.try_acquire(proxy):
                logger.debug("Acquired geo proxy country=%s proxy=%s", country, proxy)
                return proxy
        logger.info("All %s proxies on cooldown, waiting...", country)
        return self.rate_limiter.wait_and_acquire(healthy, timeout=timeout)

    def get_geo_stats(self) -> dict[str, dict]:
        stats = {}
        for country, proxies in self.geo_index.items():
            healthy = [p for p in proxies if self._is_healthy(p)]
            blocked = [p for p in proxies if p in self.blocked_proxies]
            bad = [p for p in proxies if p in self.bad_proxies]
            stats[country] = {
                "total": len(proxies),
                "healthy": len(healthy),
                "blocked": len(blocked),
                "bad": len(bad),
            }
        all_geo = {p for proxies in self.geo_index.values() for p in proxies}
        untagged = [p for p in self.proxies if p not in all_geo]
        if untagged:
            healthy_u = [p for p in untagged if self._is_healthy(p)]
            stats["UNTAGGED"] = {
                "total": len(untagged),
                "healthy": len(healthy_u),
                "blocked": len([p for p in untagged if p in self.blocked_proxies]),
                "bad": len([p for p in untagged if p in self.bad_proxies]),
            }
        return stats
