import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlparse

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

# Private / dangerous IP ranges per RFC 1918, RFC 5735, RFC 3927
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),  # RFC 1918 private
    ipaddress.ip_network("172.16.0.0/12"),  # RFC 1918 private
    ipaddress.ip_network("192.168.0.0/16"),  # RFC 1918 private
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("169.254.0.0/16"),  # link-local (AWS/GCP/Azure metadata)
    ipaddress.ip_network("fd00::/8"),  # IPv6 unique local
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
    ipaddress.ip_network("0.0.0.0/8"),  # "This" network
    ipaddress.ip_network("100.64.0.0/10"),  # Shared address space (CGN)
    ipaddress.ip_network("192.0.2.0/24"),  # TEST-NET-1
    ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),  # TEST-NET-3
    ipaddress.ip_network("240.0.0.0/4"),  # Reserved
    ipaddress.ip_network("255.255.255.255/32"),  # Broadcast
]

# Known cloud metadata hostnames / IP
_BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.goog",
}

_METADATA_IPS = {
    "169.254.169.254",  # AWS / GCP / Azure / DigitalOcean metadata
    "fd00:ec2::254",  # AWS IPv6 metadata
}


class SSRFError(ValueError):
    """Raised when a URL resolves to a private/reserved/metadata IP address."""


def _is_ip_blocked(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    if ip_str in _METADATA_IPS:
        return True

    for network in _BLOCKED_NETWORKS:
        if addr in network:
            return True
    return False


def _check_host_blocked(host: str) -> None:
    """Check hostname against the static blocklist. Raises SSRFError on match."""
    if host.lower() in _BLOCKED_HOSTNAMES:
        logger.warning("SSRF blocked: known metadata hostname %s", host)
        raise SSRFError(f"Blocked: resolves to private/reserved IP ({host})")


def _check_addrs_blocked(host: str, addr_infos: list) -> None:
    """Raise SSRFError if any resolved address is blocked."""
    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip = sockaddr[0]
        if _is_ip_blocked(ip):
            logger.warning("SSRF blocked: %s resolved to private/reserved IP %s", host, ip)
            raise SSRFError(f"Blocked: resolves to private/reserved IP ({ip})")


def validate_url_against_ssrf(url: str) -> None:
    """
    Resolve the URL's hostname to IP(s) and block any that fall into
    private/reserved/metadata ranges.

    Raises HTTPException 422 if the URL is considered unsafe.
    """
    parsed = urlparse(url)
    host: str | None = parsed.hostname

    if not host:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid URL: cannot extract hostname.",
        )

    # Block known metadata hostnames before DNS resolution
    _check_host_blocked(host)

    # Resolve and check every returned IP
    try:
        addr_infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        logger.warning("SSRF guard: DNS resolution failed for %s: %s", host, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot resolve hostname '{host}'.",
        ) from exc

    _check_addrs_blocked(host, list(addr_infos))


async def async_validate_ssrf(url: str) -> None:
    """Async variant: validate a URL against SSRF using the running event loop.

    Uses ``asyncio.get_running_loop().getaddrinfo`` for non-blocking DNS
    resolution. Raises SSRFError on violation.
    """
    parsed = urlparse(url)
    host: str | None = parsed.hostname

    if not host:
        raise SSRFError("Invalid URL: cannot extract hostname.")

    _check_host_blocked(host)

    loop = asyncio.get_running_loop()
    try:
        addr_infos = await loop.getaddrinfo(host, None)
    except OSError as exc:
        raise SSRFError(f"DNS resolution failed: {host}") from exc

    _check_addrs_blocked(host, list(addr_infos))
