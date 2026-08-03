"""Core types for fetcher implementations: FetchResult, FetcherProtocol, retry logic."""

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from uuid import UUID

logger = logging.getLogger(__name__)

# ── Block detection keywords (case-insensitive substring match on first 64KB) ──
_BLOCK_KEYWORDS: dict[str, str] = {
    "captcha": "captcha",
    "verify you are human": "captcha",
    "cf-challenge": "bot_detection",
    "robot": "bot_detection",
    "automated": "bot_detection",
    "bot detected": "bot_detection",
    "access denied": "ip_ban",
    "has been blocked": "ip_ban",
}


def _detect_block(status_code: int, body: bytes) -> tuple[bool, str | None]:
    """Detect whether a response indicates blocking / CAPTCHA.

    Returns ``(blocked, reason)`` where *reason* is one of:
    ``"captcha"``, ``"bot_detection"``, ``"ip_ban"``, ``"rate_limited"``,
    or ``None`` if not blocked.
    """
    # HTTP-level signals.
    if status_code == 429:
        return True, "rate_limited"
    if status_code in (403, 503):
        if status_code == 403:
            return True, "ip_ban"
        return True, "ip_ban"

    # Content-level signals (first 64KB only).
    if status_code == 200 and body:
        snippet = body[:65536].decode("utf-8", "replace").lower()
        for keyword, reason in _BLOCK_KEYWORDS.items():
            if keyword in snippet:
                return True, reason

    return False, None


# ── FetchResult dataclass ────────────────────────────────────────────────────


@dataclass
class FetchResult:
    url: str
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    encoding: str = "utf-8"
    elapsed_ms: int = 0
    proxy_id: UUID | None = None
    engine: str = "httpx"
    blocked: bool = False
    block_reason: str | None = None
    retries_used: int = 0
    trace_id: str | None = None
    # Raw transport bytes and headers for WARC archival (not normalized).
    raw_body: bytes = b""
    raw_headers: dict[str, str] = field(default_factory=dict)


# ── FetchError ────────────────────────────────────────────────────────────────


class FetchError(Exception):
    """Raised when all retries are exhausted or SSRF guard blocks the URL."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        blocked: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.blocked = blocked


# ── FetcherProtocol ───────────────────────────────────────────────────────────


@runtime_checkable
class FetcherProtocol(Protocol):
    async def fetch(
        self,
        url: str,
        *,
        proxy: object | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float = 30.0,
        follow_redirects: bool = True,
        max_redirects: int = 10,
    ) -> FetchResult: ...


# ── Retry orchestration ──────────────────────────────────────────────────────


def _jittered_delay(policy) -> float:
    """Return a random delay in seconds between policy's min/max delay ms."""
    min_ms = policy.min_delay_ms if policy else 500
    max_ms = policy.max_delay_ms if policy else 2000
    return random.uniform(min_ms, max_ms) / 1000.0


def _normalize_domain_from_url(url: str) -> str:
    from urllib.parse import urlparse

    from app.services.policy_resolver import normalize_domain

    parsed = urlparse(url)
    return normalize_domain(parsed.hostname or url)


async def fetch_with_retry(
    fetcher: "FetcherProtocol",
    url: str,
    *,
    policy=None,
    proxy_manager=None,
    db=None,
    sticky_key: str | None = None,
    trace_id: str | None = None,
    use_proxy: bool | None = None,
    proxy_country: str | None = None,
) -> FetchResult:
    """Retry loop with proxy selection, health reporting, and jittered backoff.

    Up to ``policy.max_retries`` (default 3) attempts.  On each failure the
    proxy is reported via ``proxy_manager.report_result`` and a jittered
    delay is inserted before the next attempt.

    Proxy policy (three-level resolution):
    1. Explicit *use_proxy* argument (from request options) — highest priority.
    2. ``policy.use_proxy`` (from DomainPolicy row).
    3. Defaults to ``True`` if no policy row exists.

    When ``use_proxy=True`` and no healthy proxy is available the function
    raises :class:`ProxyPoolUnavailableError` rather than silently falling
    back to a direct connection.  Blocked proxies are tracked in
    ``failed_proxy_ids`` and excluded from subsequent retry picks — when all
    eligible proxies are exhausted the function raises
    :class:`ProxyPoolExhaustedError`.
    """
    from app.core.errors import ProxyPoolExhaustedError, ProxyPoolUnavailableError

    domain = _normalize_domain_from_url(url)
    max_retries = policy.max_retries if policy else 3

    # ── Resolve effective proxy policy ──────────────────────────────────────
    effective_use_proxy = (
        use_proxy if use_proxy is not None else (policy.use_proxy if policy else False)
    )
    effective_country = (
        proxy_country if proxy_country is not None else (policy.proxy_country if policy else None)
    )

    last_error: Exception | None = None
    failed_proxy_ids: set[UUID] = set()

    for attempt in range(max_retries):
        proxy = None
        try:
            # 1. Pick proxy.
            if effective_use_proxy and proxy_manager is not None:
                proxy = await proxy_manager.get_proxy(
                    pool_id=policy.proxy_pool_id if policy else None,
                    domain=domain,
                    sticky_key=sticky_key if attempt == 0 else None,
                    exclude_ids=failed_proxy_ids,
                    country=effective_country,
                )

                # Fail-fast: caller requested proxy but none is available.
                if proxy is None:
                    if failed_proxy_ids:
                        raise ProxyPoolExhaustedError(
                            f"PROXY_POOL_EXHAUSTED: all eligible "
                            f"{effective_country or 'ANY'} proxies were "
                            f"blocked or unhealthy for domain={domain}"
                        )
                    raise ProxyPoolUnavailableError(
                        f"PROXY_POOL_EMPTY: no healthy proxy for "
                        f"domain={domain}, "
                        f"country={effective_country or 'ANY'}"
                    )

            # 2. Build headers.
            from app.services.fetchers.headers import headers_for_domain

            merged_headers = headers_for_domain(policy)

            # 3. Fetch.
            result = await fetcher.fetch(
                url,
                proxy=proxy,
                headers=merged_headers,
                timeout_s=30.0,
            )
            result.retries_used = attempt
            result.trace_id = trace_id

            # 4. Check for block.
            if result.blocked:
                if proxy_manager is not None and proxy is not None:
                    await proxy_manager.report_result(
                        proxy_id=proxy.id,
                        domain=domain,
                        success=False,
                        reason=result.block_reason or "http_error",
                        db=db,
                    )
                    # Rotate: exclude this proxy and try another.
                    failed_proxy_ids.add(proxy.id)
                if attempt < max_retries - 1:
                    await asyncio.sleep(_jittered_delay(policy))
                    continue
                return result

            # 5. Success.
            if proxy_manager is not None and proxy is not None:
                await proxy_manager.report_result(
                    proxy_id=proxy.id,
                    domain=domain,
                    success=True,
                    reason=None,
                    db=db,
                )
            return result

        except (ProxyPoolUnavailableError, ProxyPoolExhaustedError):
            # These are hard failures — do not retry.
            raise

        except FetchError as exc:
            last_error = exc
            if proxy_manager is not None and proxy is not None:
                await proxy_manager.report_result(
                    proxy_id=proxy.id,
                    domain=domain,
                    success=False,
                    reason="http_error",
                    db=db,
                )
                failed_proxy_ids.add(proxy.id)
            if attempt < max_retries - 1:
                await asyncio.sleep(_jittered_delay(policy))
                continue
            raise

        except Exception as exc:
            last_error = exc
            if proxy is not None and proxy_manager is not None:
                failed_proxy_ids.add(proxy.id)
            if attempt < max_retries - 1:
                await asyncio.sleep(_jittered_delay(policy))
                continue
            raise FetchError(str(exc)) from exc

    # Should not reach here — last retry loops either return or raise.
    raise FetchError(str(last_error)) from last_error
