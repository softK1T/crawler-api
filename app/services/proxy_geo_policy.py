"""Pure country/discovery policy shared by worker and proxy selection."""
from datetime import UTC, datetime, timedelta
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit


def public_ip(value: str) -> str:
    ip = ipaddress.ip_address(value)
    if not ip.is_global or ip.is_multicast or ip.is_unspecified:
        raise ValueError("non_public_ip")
    return str(ip)


def country_code(value: str) -> str:
    code = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", code):
        raise ValueError("invalid_country")
    return code


def probe_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError("invalid_probe_endpoint")
    public_ip(parsed.hostname)
    return url


def pin_proxy(url: str, addresses: list[str]) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https", "socks5", "socks5h", "socks4", "socks4a"}
            or not parsed.hostname or parsed.port is None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("invalid_proxy_url")
    if not addresses:
        raise ValueError("proxy_dns_empty")
    checked = [public_ip(address) for address in addresses]
    address = checked[0]
    host = f"[{address}]" if ":" in address else address
    auth = parsed.netloc.rsplit("@", 1)[0] + "@" if "@" in parsed.netloc else ""
    return urlunsplit((parsed.scheme, f"{auth}{host}:{parsed.port}", "", "", ""))


def parse_exit_ip(body: bytes) -> str:
    if len(body) > 16384:
        raise ValueError("probe_response_too_large")
    matches = [line[3:] for line in body.decode("ascii").splitlines() if line.startswith("ip=")]
    if len(matches) != 1:
        raise ValueError("invalid_probe_response")
    return public_ip(matches[0].strip())


def selection_matches(proxy, *, pool_id=None, country=None, city=None,
                      proxy_type=None, now=None) -> bool:
    now = now or datetime.now(UTC)
    if not proxy.is_active:
        return False
    if proxy.cooldown_until is not None and proxy.cooldown_until > now:
        return False
    if pool_id is not None and proxy.pool_id != pool_id:
        return False
    if country is not None and proxy.country != country_code(country):
        return False
    if city is not None and proxy.city != city:
        return False
    if proxy_type is not None and proxy.proxy_type != proxy_type:
        return False
    if getattr(proxy, "geo_enabled", False):
        if (proxy.geo_status not in {"verified", "mismatch"}
                or proxy.geo_checked_at is None or proxy.geo_next_check_at is None
                or proxy.geo_next_check_at <= now):
            return False
    return True


def apply_observation(proxy, *, now, retry_s: int, ttl_s: int,
                      exit_ip=None, country=None, error=None) -> None:
    proxy.geo_attempted_at = now
    if error is not None:
        proxy.geo_status = "failed"
        proxy.geo_last_error = error
        proxy.geo_next_check_at = now + timedelta(seconds=retry_s)
        return
    proxy.exit_ip = public_ip(exit_ip)
    proxy.detected_country = country_code(country) if country else None
    proxy.geo_source = "geolite2-country"
    proxy.geo_last_error = None
    if not country:
        proxy.geo_status = "unknown"
        proxy.geo_next_check_at = now + timedelta(seconds=retry_s)
        return
    code = country_code(country)
    if proxy.declared_country is None and proxy.geo_checked_at is None:
        proxy.declared_country = proxy.country
    if proxy.country != code:
        proxy.city = None
    proxy.country = code
    proxy.geo_status = "mismatch" if proxy.declared_country and proxy.declared_country != code else "verified"
    proxy.geo_checked_at = now
    proxy.geo_next_check_at = now + timedelta(seconds=ttl_s)
