"""Bounded HTTPS exit-IP probe. No target-site requests or direct fallback."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from app.services.proxy_geo_policy import parse_exit_ip, pin_proxy, probe_endpoint


async def fetch_trace_body(proxy_url: str, endpoint: str, timeout_s: float) -> bytes:
    from curl_cffi.requests import AsyncSession

    endpoint = probe_endpoint(endpoint)
    parsed = urlsplit(proxy_url)
    if not parsed.hostname or parsed.port is None:
        raise ValueError("invalid_proxy_url")
    # Validate URL syntax before doing any DNS work.
    pin_proxy(proxy_url, ["1.1.1.1"])

    async with asyncio.timeout(timeout_s):
        try:
            addresses = [str(ipaddress.ip_address(parsed.hostname))]
        except ValueError:
            infos = await asyncio.get_running_loop().getaddrinfo(
                parsed.hostname, parsed.port, type=socket.SOCK_STREAM
            )
            addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
        pinned = pin_proxy(proxy_url, addresses)
        body = bytearray()

        def collect(chunk: bytes) -> int:
            if len(body) + len(chunk) > 16384:
                raise ValueError("probe_response_too_large")
            body.extend(chunk)
            return len(chunk)

        async with AsyncSession(trust_env=False) as session:
            response = await session.get(
                endpoint,
                proxy=pinned,
                timeout=timeout_s,
                verify=True,
                allow_redirects=False,
                content_callback=collect,
            )
        if response.status_code != 200:
            raise ValueError("probe_http_error")
        return bytes(body)


def lookup_country(path: str, exit_ip: str) -> str | None:
    import maxminddb

    with maxminddb.open_database(path) as reader:
        entry = reader.get(exit_ip)

    # Do not substitute registered_country: it is not exit-IP geolocation.
    if not isinstance(entry, dict):
        return None
    country = entry.get("country")
    if not isinstance(country, dict):
        return None
    code = country.get("iso_code")
    return code if isinstance(code, str) else None


def check_database(path: str) -> None:
    import maxminddb

    with maxminddb.open_database(path) as reader:
        if "Country" not in reader.metadata().database_type:
            raise ValueError("country_database_required")


async def discover_exit_ip(proxy_url: str, endpoint: str, timeout_s: float) -> str:
    return parse_exit_ip(await fetch_trace_body(proxy_url, endpoint, timeout_s))
