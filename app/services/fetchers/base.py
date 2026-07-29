"""Core types for fetcher implementations: FetchResult, FetcherProtocol, retry logic."""

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
) -> FetchResult:
    """Retry loop with proxy selection, health reporting, and jittered backoff.

    Up to ``policy.max_retries`` (default 3) attempts.  On each failure the
    proxy is reported via ``proxy_manager.report_result`` and a jittered
    delay is inserted before the next attempt.
    """
    import asyncio

    max_retries = policy.max_retries if policy else 3
    last_error: Exception | None = None

    for attempt in range(max_retries):
        proxy = None
        try:
            # 1. Pick proxy.
            if proxy_manager is not None and policy is not None:
                domain = _normalize_domain_from_url(url)
                proxy = await proxy_manager.get_proxy(
                    pool_id=policy.proxy_pool_id,
                    domain=domain,
                    sticky_key=sticky_key,
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
                        domain=_normalize_domain_from_url(url),
                        success=False,
                        reason=result.block_reason or "http_error",
                        db=db,
                    )
                if attempt < max_retries - 1:
                    await asyncio.sleep(_jittered_delay(policy))
                    continue
                return result

            # 5. Success.
            if proxy_manager is not None and proxy is not None:
                await proxy_manager.report_result(
                    proxy_id=proxy.id,
                    domain=_normalize_domain_from_url(url),
                    success=True,
                    reason=None,
                    db=db,
                )
            return result

        except FetchError as exc:
            last_error = exc
            if proxy_manager is not None and proxy is not None:
                await proxy_manager.report_result(
                    proxy_id=proxy.id,
                    domain=_normalize_domain_from_url(url),
                    success=False,
                    reason="http_error",
                    db=db,
                )
            if attempt < max_retries - 1:
                await asyncio.sleep(_jittered_delay(policy))
                continue
            raise

        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                await asyncio.sleep(_jittered_delay(policy))
                continue
            raise FetchError(str(exc)) from exc

    # Should not reach here — last retry loops either return or raise.
    raise FetchError(str(last_error)) from last_error
