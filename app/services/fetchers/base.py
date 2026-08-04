"""Core types for fetcher implementations: FetchResult, FetcherProtocol, retry logic."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

if TYPE_CHECKING:
    from app.models.domain_policy import DomainPolicy
    from app.services.proxy_manager import ProxyManager
    from app.worker.browser_pool import BrowserPool

logger = logging.getLogger(__name__)


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
    # Escalation tier at which this result was produced (set by fetch_with_retry).
    _tier_used: int = 0


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


def _jittered_delay(policy: object) -> float:
    """Return a random delay in seconds between policy's min/max delay ms."""
    min_ms = getattr(policy, "min_delay_ms", None) or 500
    max_ms = getattr(policy, "max_delay_ms", None) or 2000
    return random.uniform(min_ms, max_ms) / 1000.0


def _normalize_domain_from_url(url: str) -> str:
    from urllib.parse import urlparse

    from app.services.policy_resolver import normalize_domain

    parsed = urlparse(url)
    return normalize_domain(parsed.hostname or url)


@dataclass
class _EscalationState:
    """Mutable escalation state kept across retry-loop iterations."""

    tier: int
    attempts_at_tier: int = 0
    fetcher: FetcherProtocol | None = None  # current engine instance


async def fetch_with_retry(
    fetcher: FetcherProtocol,
    url: str,
    *,
    policy: DomainPolicy | None = None,
    proxy_manager: ProxyManager | None = None,
    db: object = None,
    sticky_key: str | None = None,
    trace_id: str | None = None,
    use_proxy: bool | None = None,
    proxy_country: str | None = None,
    proxy_type: str | None = None,
    browser_pool: BrowserPool | None = None,
) -> FetchResult:
    """Retry loop with proxy selection, health reporting, jittered backoff,
    and adaptive engine escalation.

    Attempt ceiling
    ---------------
    ``policy.max_escalation_attempts`` (default 12) is the hard ceiling on
    TOTAL attempts across all tiers.  ``policy.max_retries`` (default 3) is
    preserved as the per-tier attempt cap for non-escalating callers — callers
    that do not pass a policy still get max_retries behaviour unchanged.

    Proxy/engine precedence (four-level, outermost wins)
    ----------------------------------------------------
    1. Explicit *use_proxy* / *proxy_type* arguments from the API request.
    2. Escalation ladder tier (engine + proxy_type), derived from policy.
    3. ``policy.use_proxy`` / ``policy.proxy_type`` (DomainPolicy row).
    4. Defaults: use_proxy=False, proxy_type=datacenter.

    Escalation rules
    ----------------
    - Start at escalation.initial_tier(policy) — respects learned tier and
      vendor floor, so a known-Kasada domain never wastes attempts at tier 0.
    - Allow MAX_ATTEMPTS_PER_TIER (2) attempts before bumping the tier.
    - Only bump when block_reason is in ESCALATABLE (vendor challenges).
      IP_BAN / RATE_LIMITED only rotate the proxy — engine stays the same.
    - On tier change: clear failed_proxy_ids (a new proxy_type invalidates
      prior IP bans) and re-instantiate the fetcher if the engine changed.
    - Premium tiers (residential/mobile) are gated behind
      settings.enable_premium_proxy_tiers (default False).  When the flag is
      off, escalation stops at the highest free tier, logs a warning, and
      returns the last blocked FetchResult rather than raising.
    - Tier-0 direct-connection: preserved — if proxy is None and block occurs,
      we now escalate instead of hard-returning, unless caller explicitly
      forced use_proxy=False (which locks tier 0).

    When ``use_proxy=True`` and no healthy proxy is available the function
    raises :class:`ProxyPoolUnavailableError` rather than silently falling
    back to a direct connection.  Blocked proxies are tracked in
    ``failed_proxy_ids`` and excluded from subsequent retry picks.
    """
    from app.core.errors import ProxyPoolExhaustedError, ProxyPoolUnavailableError
    from app.services.escalation import (
        LADDER,
        MAX_ATTEMPTS_PER_TIER,
        effective_max_tier,
        initial_tier,
        is_escalatable,
        next_tier,
    )
    from app.services.fetchers import get_fetcher

    domain = _normalize_domain_from_url(url)

    # ── Settings for premium gate ────────────────────────────────────────────
    try:
        from app.core.config import settings as _settings

        enable_premium = _settings.enable_premium_proxy_tiers
    except Exception:
        enable_premium = False

    max_tier = effective_max_tier(enable_premium)
    max_attempts = getattr(policy, "max_escalation_attempts", None) or 12

    # ── Caller-level overrides (level 1 precedence) ──────────────────────────
    # When the caller explicitly sets use_proxy / proxy_type, those values
    # override the ladder for the entire call.  Engine still escalates.
    caller_forced_use_proxy = use_proxy  # None means "let ladder decide"
    caller_forced_proxy_type = proxy_type  # None means "let ladder decide"

    # ── Country resolution (unchanged from original) ─────────────────────────
    effective_country = (
        proxy_country if proxy_country is not None else (getattr(policy, "proxy_country", None))
    )
    if effective_country is not None:
        effective_country = effective_country.strip().upper()

    # ── Escalation state ─────────────────────────────────────────────────────
    start_tier = min(initial_tier(policy), max_tier)
    esc = _EscalationState(tier=start_tier, fetcher=fetcher)

    last_result: FetchResult | None = None
    last_error: Exception | None = None
    failed_proxy_ids: set[UUID] = set()
    total_attempts = 0

    while total_attempts < max_attempts:
        # Clamp tier to max_tier (premium gate).
        if esc.tier > max_tier:
            logger.warning(
                "escalation_premium_gate_hit",
                extra={
                    "domain": domain,
                    "tier": esc.tier,
                    "max_tier": max_tier,
                    "reason": "enable_premium_proxy_tiers=False",
                },
            )
            if last_result is not None:
                return last_result
            break

        tier_def = LADDER[esc.tier]

        # ── Derive effective proxy settings for this tier ────────────────────
        # Caller-forced values win; otherwise use ladder.
        tier_use_proxy = (
            caller_forced_use_proxy if caller_forced_use_proxy is not None else tier_def.use_proxy
        )
        tier_proxy_type = (
            caller_forced_proxy_type
            if caller_forced_proxy_type is not None
            else tier_def.proxy_type
        )

        # ── Re-instantiate fetcher when engine changes ───────────────────────
        if esc.fetcher is None or getattr(esc.fetcher, "_engine_name", None) != tier_def.engine:
            esc.fetcher = get_fetcher(tier_def.engine, browser_pool=browser_pool)

        current_fetcher = esc.fetcher
        proxy = None
        total_attempts += 1

        try:
            from app.core.observability import FETCH_ATTEMPTS_BY_TIER

            FETCH_ATTEMPTS_BY_TIER.labels(tier=str(esc.tier), engine=LADDER[esc.tier].engine).inc()
        except Exception:  # noqa: S110
            pass

        try:
            # 1. Pick proxy.
            if tier_use_proxy and proxy_manager is not None:
                proxy = await proxy_manager.get_proxy(
                    pool_id=getattr(policy, "proxy_pool_id", None),
                    domain=domain,
                    sticky_key=sticky_key if total_attempts == 1 else None,
                    exclude_ids=failed_proxy_ids,
                    country=effective_country,
                    proxy_type=tier_proxy_type,
                )

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
            result = await current_fetcher.fetch(
                url,
                proxy=proxy,
                headers=merged_headers,
                timeout_s=30.0,
            )
            result.retries_used = total_attempts - 1
            result.trace_id = trace_id

            # 4. Check for block.
            if result.blocked:
                last_result = result
                esc.attempts_at_tier += 1

                if proxy_manager is not None and proxy is not None:
                    await proxy_manager.report_result(
                        proxy_id=proxy.id,
                        domain=domain,
                        success=False,
                        reason=result.block_reason or "http_error",
                        db=db,
                    )
                    failed_proxy_ids.add(proxy.id)

                # Decide: escalate tier or rotate proxy?
                if esc.attempts_at_tier >= MAX_ATTEMPTS_PER_TIER and is_escalatable(
                    result.block_reason
                ):
                    nxt = next_tier(esc.tier)
                    if nxt is None or nxt > max_tier:
                        # Top of reachable ladder — return last blocked result.
                        logger.warning(
                            "escalation_ladder_exhausted",
                            extra={"domain": domain, "tier": esc.tier},
                        )
                        result._tier_used = esc.tier
                        return result
                    logger.info(
                        "escalation_tier_bump",
                        extra={
                            "domain": domain,
                            "from_tier": esc.tier,
                            "to_tier": nxt,
                            "reason": result.block_reason,
                        },
                    )
                    esc.tier = nxt
                    esc.attempts_at_tier = 0
                    esc.fetcher = None  # force re-instantiation
                    failed_proxy_ids.clear()  # new proxy_type — reset bans
                    # No sleep between tier bumps — the new engine is the retry.
                    continue

                # Rotation-only block (IP_BAN / RATE_LIMITED) or within-tier retry.
                if proxy is None and caller_forced_use_proxy is not True:
                    # Direct connection blocked and caller didn't force proxy —
                    # escalate out of tier 0 rather than hard-returning.
                    nxt = next_tier(esc.tier)
                    if nxt is not None and nxt <= max_tier:
                        esc.tier = nxt
                        esc.attempts_at_tier = 0
                        esc.fetcher = None
                        continue
                    return result

                if total_attempts < max_attempts:
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
            if total_attempts < max_attempts:
                await asyncio.sleep(_jittered_delay(policy))
                continue
            raise

        except Exception as exc:
            last_error = exc
            if proxy is not None and proxy_manager is not None:
                failed_proxy_ids.add(proxy.id)
            if total_attempts < max_attempts:
                await asyncio.sleep(_jittered_delay(policy))
                continue
            raise FetchError(str(exc)) from exc

    raise FetchError(str(last_error)) from last_error
