import logging
from typing import Dict, List, Optional
from urllib.parse import urlparse

from app.services.crawler import SmartProxyPool

logger = logging.getLogger(__name__)

TLD_COUNTRY_MAP: Dict[str, str] = {
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
    """
    Auto-detect the most relevant country for a URL based on its TLD.
    Multi-part TLDs (.co.uk, .com.br) are checked before single TLDs.
    Falls back to 'US' for .com / .org / .net and unknown TLDs.
    """
    try:
        host = urlparse(url).netloc.lower()
        # Strip port if present
        host = host.split(":")[0]
        # Check multi-part TLDs first (sorted by length desc in TLD_COUNTRY_MAP)
        for tld, country in sorted(TLD_COUNTRY_MAP.items(), key=lambda x: -len(x[0])):
            if host.endswith(tld):
                return country
    except Exception:
        pass
    return "US"


class GeoProxyPool(SmartProxyPool):
    """
    Extends SmartProxyPool with country-based proxy selection.

    Proxy file extended format (5 parts):
        host:port:user:pass:COUNTRY

    Example proxies.txt:
        1.2.3.4:8080:user:pass:US
        5.6.7.8:3128:user2:pass2:DE
        9.10.11.12:8080:user3:pass3:PL

    Standard 2-part and 4-part formats still supported (no country tag).
    """

    def __init__(self, proxy_list: List[str], **kwargs):
        # geo_index: country -> list of clean proxy lines (without country tag)
        self.geo_index: Dict[str, List[str]] = {}
        clean_proxies: List[str] = []

        for line in proxy_list:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split(":")
            if len(parts) == 5:
                # host:port:user:pass:COUNTRY
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

    def pick_proxy_for_country(self, country: str, timeout: float = 60) -> Optional[str]:
        """
        Pick the healthiest available proxy for the given country.
        Falls back to any healthy proxy if no geo match found.
        """
        country = country.upper()
        candidates = self.geo_index.get(country, [])
        healthy = [p for p in candidates if self._is_healthy(p)]

        if not healthy:
            logger.debug("No healthy proxies for country=%s, falling back to global pool", country)
            return self.pick_proxy_line(timeout=timeout)

        # Sort by success rate (best first)
        healthy.sort(key=lambda p: self.proxy_success_rate.get(p, 1.0), reverse=True)

        for proxy in healthy:
            if self.rate_limiter.try_acquire(proxy):
                logger.debug("Acquired geo proxy country=%s proxy=%s", country, proxy)
                return proxy

        # All healthy geo proxies on cooldown — wait
        logger.info("All %s proxies on cooldown, waiting...", country)
        return self.rate_limiter.wait_and_acquire(healthy, timeout=timeout)

    def get_geo_stats(self) -> Dict[str, Dict]:
        """Return per-country proxy counts and health breakdown."""
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
        # Also include untagged proxies
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
