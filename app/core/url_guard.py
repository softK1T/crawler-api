"""Outbound URL policy for the fetch path.

``ssrf_guard.validate_url_against_ssrf`` only inspected the *initial* URL and did
so with a blocking resolver. Two gaps followed from that:

1. A redirect to http://169.254.169.254/ was followed unchecked, because httpx
   was configured with ``follow_redirects=True`` and no per-hop callback.
2. ``socket.getaddrinfo`` stalled the event loop for the whole DNS timeout.

This module owns scheme/port/redirect/body policy. Blocked IP ranges are NOT
redefined here: ``ssrf_guard`` stays the single source of truth for them.
"""

import ipaddress
import logging
from urllib.parse import urlparse

# Reaching into the private helper is deliberate: duplicating the range table
# would let the two copies drift apart, which is worse than the import.
from app.core.ssrf_guard import (
    _BLOCKED_HOSTNAMES,
    SSRFError,
    _is_ip_blocked,
    async_validate_ssrf,
)

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Crawling a target on an arbitrary port is a lateral-movement primitive
# (redis:6379, postgres:5432, docker:2375). Only web ports are permitted.
ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})

MAX_REDIRECTS = 10
MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MiB — a product page that exceeds this is a trap


class URLGuardError(ValueError):
    """Raised for a URL that violates outbound policy. Callers map it to HTTP 422."""


class BodyTooLarge(ValueError):
    """Raised when a response body exceeds MAX_BODY_BYTES."""


# Backward-compatible alias — existing import sites continue to work.
UrlNotAllowed = URLGuardError


def _check_static(url: str) -> tuple[str, int]:
    """Validate everything that needs no DNS. Returns (hostname, port)."""
    parsed = urlparse(url)

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise URLGuardError(f"Scheme '{parsed.scheme}' is not allowed; use http or https.")

    host = parsed.hostname
    if not host:
        raise URLGuardError("Invalid URL: cannot extract hostname.")

    if host.lower() in _BLOCKED_HOSTNAMES:
        raise URLGuardError(f"Hostname '{host}' is a known metadata endpoint.")

    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise URLGuardError(f"Port {port} is not allowed; permitted ports: {sorted(ALLOWED_PORTS)}")

    # A literal IP skips DNS entirely and must be checked here.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if _is_ip_blocked(host):
            raise URLGuardError(f"IP {host} is in a private or reserved range.")

    return host, port


def _check_resolved(host: str, addr_infos: list) -> None:
    """Reject the host if ANY resolved address is blocked.

    All-or-nothing on purpose: a hostname resolving to one public and one
    private address is a rebinding attempt, not a misconfiguration.
    """
    for info in addr_infos:
        ip = info[4][0]
        if _is_ip_blocked(ip):
            logger.warning("url_guard: %s resolved to blocked address %s", host, ip)
            raise URLGuardError(f"Host '{host}' resolves to blocked address {ip}.")


def validate_url_sync(url: str) -> None:
    """Blocking validation. For worker/redirect paths that are already sync."""
    import socket

    host, _port = _check_static(url)
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise URLGuardError(f"DNS resolution failed: {host}") from exc
    _check_resolved(host, list(infos))


async def validate_url_async(url: str) -> None:
    """Non-blocking validation for use inside FastAPI request handlers.

    Uses async_validate_ssrf for per-hop DNS resolution — the sync
    ``socket.getaddrinfo`` path is never called from an async context.
    """
    _host, _port = _check_static(url)
    # Delegate to ssrf_guard's async variant for DNS + blocked-range checks.
    try:
        await async_validate_ssrf(url)
    except SSRFError as exc:
        raise URLGuardError(str(exc)) from exc
