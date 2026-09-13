"""Core types for fetcher implementations: FetchResult, FetcherProtocol, retry logic."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable
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
    # Persisted request_log row id of the attempt that produced this result
    # (set by fetch_with_retry via the attempt recorder).  Private — must not
    # become part of the public API schema.
    _request_log_id: UUID | None = field(default=None, repr=False)


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


# ── Per-attempt observability record ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AttemptResult:
    """Transport-neutral record of ONE fetch attempt (retry-loop iteration).

    Built by fetch_with_retry and handed to the caller-provided
    ``attempt_recorder`` exactly once per attempt, from a finally block.
    Deliberately contains no request/response headers, bodies, cookies or
    proxy URLs — only safe proxy metadata snapshots.
    """

    url: str
    domain: str
    method: str
    attempt_number: int
    tier_attempt_number: int
    escalation_tier: int
    proxy_id: UUID | None
    proxy_pool_id: UUID | None
    proxy_provider: str | None
    proxy_type: str | None
    proxy_country: str | None
    proxy_city: str | None
    engine: str
    outcome: str
    status_code: int | None
    duration_ms: int
    bytes_received: int
    blocked: bool
    block_reason: str | None
    error_type: str | None
    error: str | None
    requested_at: datetime
    completed_at: datetime


AttemptRecorder = Callable[[AttemptResult], Awaitable[UUID | None]]


def classify_fetch_error(exc: FetchError) -> str:
    """Map a FetchError to a request_log outcome."""
    if exc.blocked:
        return "blocked"
    return "fetch_error"


def classify_exception(exc: Exception) -> str:
    """Map an unexpected exception to a request_log outcome."""
    if isinstance(exc, asyncio.TimeoutError):
        return "network_error"
    name = type(exc).__name__.lower()
    if any(token in name for token in ("timeout", "timedout", "network", "connection")):
        return "network_error"
    return "internal_error"


def proxy_failure_reason(exc: BaseException) -> str:
    """Map an attempt failure to a proxy-health reason (conservative)."""
    if isinstance(exc, (asyncio.CancelledError, asyncio.TimeoutError)):
        return "timeout"
    name = type(exc).__name__.lower()
    if "timeout" in name or "timedout" in name:
        return "timeout"
    return "http_error"


async def _report_proxy_attempt(
    *,
    proxy,
    proxy_manager,
    domain: str,
    success: bool,
    reason: str | None,
    db,
    engine: str | None,
    response_time_ms: int | None = None,
) -> bool:
    """Call ``report_result`` for the attempt's selected proxy, at most once.

    Returns True when the report succeeded (so callers mark it reported).
    A proxy-health update failure is logged as an instrumentation error and
    swallowed — it must never suppress request-attempt persistence or change
    the attempt's outcome.  Cancellation still propagates.
    """
    try:
        await proxy_manager.report_result(
            proxy_id=proxy.id,
            domain=domain,
            success=success,
            reason=reason,
            db=db,
            response_time_ms=response_time_ms,
            engine=engine,
        )
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("proxy_result_report_failed", exc_info=True)
        return False


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
    attempts_at_tier: int = 0  # blocked attempts at tier (drives escalation)
    fetcher: FetcherProtocol | None = None  # current engine instance
    # Every iteration at the current tier (including network errors), used
    # only to number rows in request_log.  Reset together with attempts_at_tier.
    iterations_at_tier: int = 0


async def fetch_with_retry(
    fetcher: FetcherProtocol,
    url: str,
    *,
    policy: object | None = None,
    proxy_manager: ProxyManager | None = None,
    db: object = None,
    sticky_key: str | None = None,
    trace_id: str | None = None,
    use_proxy: bool | None = None,
    proxy_country: str | None = None,
    proxy_city: str | None = None,
    proxy_type: str | None = None,
    session_key: str | None = None,
    browser_pool: BrowserPool | None = None,
    requested_engine: str | None = None,
    camoufox_ready: bool | None = None,
    attempt_recorder: AttemptRecorder | None = None,
) -> FetchResult:
    """Retry loop with proxy selection, health reporting, jittered backoff,
    and adaptive engine escalation.

    ``policy`` is accessed exclusively via ``getattr`` so any object exposing
    the expected attributes (a real ``DomainPolicy`` row, a test double, or a
    ``SimpleNamespace``) is accepted — hence the ``object | None`` annotation.

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
    - Allow ``policy.max_retries`` attempts at each tier before bumping,
      falling back to MAX_ATTEMPTS_PER_TIER (2) when the policy omits it.
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

    ``attempt_recorder`` (optional) is invoked exactly once per loop
    iteration from a finally block with an :class:`AttemptResult` snapshot —
    including direct, proxied, blocked, timed-out, pool-empty/exhausted,
    cancelled and unexpected-exception attempts.  Jitter/backoff sleep is
    excluded from the attempt duration.
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

    # policy.max_retries overrides the per-tier attempt cap (level 3 precedence).
    # Falls back to MAX_ATTEMPTS_PER_TIER when unset so existing behaviour holds.
    _policy_retries = getattr(policy, "max_retries", None)
    attempts_per_tier = (
        int(_policy_retries)
        if isinstance(_policy_retries, int) and _policy_retries > 0
        else MAX_ATTEMPTS_PER_TIER
    )

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

    # ── City resolution — same precedence as country: caller arg wins,
    # falling back to policy.proxy_city if present (most policies won't set
    # it; city targeting is primarily a request-level override). ─────────────
    effective_city = proxy_city if proxy_city is not None else (getattr(policy, "proxy_city", None))
    if effective_city is not None:
        effective_city = effective_city.strip()

    # ── Escalation state ─────────────────────────────────────────────────────
    if requested_engine is not None:
        # The caller explicitly asked for a specific engine (mode="camoufox").
        # Start at the first ladder rung using that engine instead of the
        # learned policy tier — otherwise the request would silently run the
        # cheap httpx tier and never touch the requested engine.
        start_tier = next(i for i, t in enumerate(LADDER) if t.engine == requested_engine)
    else:
        start_tier = min(initial_tier(cast("DomainPolicy | None", policy)), max_tier)
    esc = _EscalationState(tier=start_tier, fetcher=fetcher)

    last_result: FetchResult | None = None
    last_error: Exception | None = None
    failed_proxy_ids: set[UUID] = set()
    total_attempts = 0

    while total_attempts < max_attempts:
        # Clamp tier to max_tier (premium gate).  An explicit
        # requested_engine bypasses the gate for the ENGINE only: the premium
        # gate protects proxy EUR spend, not engine CPU, so the requested
        # engine runs direct (no proxy) instead of bailing out.
        tier_bypassed_premium_gate = False
        if esc.tier > max_tier:
            if requested_engine is not None and LADDER[esc.tier].engine == requested_engine:
                tier_bypassed_premium_gate = True
                logger.warning(
                    "premium_gate_bypassed_for_requested_engine",
                    extra={
                        "domain": domain,
                        "engine": requested_engine,
                        "tier": esc.tier,
                        "reason": "explicit engine request — premium proxy dropped, engine kept",
                    },
                )
            else:
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
        # Level 3: DomainPolicy row, consulted only at the ladder's base tier —
        # above tier 0 the ladder's own proxy requirement must not be weakened.
        policy_use_proxy = getattr(policy, "use_proxy", None)
        policy_proxy_type = getattr(policy, "proxy_type", None)

        if caller_forced_use_proxy is not None:
            tier_use_proxy = caller_forced_use_proxy
        elif tier_bypassed_premium_gate:
            tier_use_proxy = False
        elif tier_def.use_proxy:
            tier_use_proxy = True
        elif policy_use_proxy is not None:
            tier_use_proxy = bool(policy_use_proxy)
        else:
            tier_use_proxy = False

        tier_proxy_type: str | None
        if caller_forced_proxy_type is not None:
            tier_proxy_type = caller_forced_proxy_type
        elif tier_bypassed_premium_gate:
            tier_proxy_type = None
        elif tier_def.proxy_type is not None:
            tier_proxy_type = tier_def.proxy_type
        else:
            tier_proxy_type = policy_proxy_type

        # ── Re-instantiate fetcher when engine changes ───────────────────────
        if esc.fetcher is None or getattr(esc.fetcher, "_engine_name", None) != tier_def.engine:
            esc.fetcher = get_fetcher(
                tier_def.engine,
                browser_pool=browser_pool,
                camoufox_ready=camoufox_ready,
            )

        current_fetcher = esc.fetcher
        proxy = None
        total_attempts += 1
        esc.iterations_at_tier += 1
        tier_attempt_number = esc.iterations_at_tier
        # Snapshot the attempt identity BEFORE the try: escalation may bump
        # the tier mid-iteration, and the recorder must reflect the tier and
        # engine that actually produced this attempt.
        attempt_tier = esc.tier
        attempt_engine = tier_def.engine
        requested_at = datetime.now(UTC)
        attempt_started = time.perf_counter()
        # Frozen right before any jitter/backoff sleep so the recorded
        # duration never includes time we were not trying the network.
        attempt_end: float | None = None

        result: FetchResult | None = None
        outcome = "internal_error"
        error_type: str | None = None
        error_message: str | None = None
        proxy_result_reported = False

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
                    city=effective_city,
                    proxy_type=tier_proxy_type,
                )

                if proxy is None:
                    if failed_proxy_ids:
                        raise ProxyPoolExhaustedError(
                            f"PROXY_POOL_EXHAUSTED: all eligible "
                            f"{effective_country or 'ANY'}"
                            f"{'/' + effective_city if effective_city else ''} proxies were "
                            f"blocked or unhealthy for domain={domain}"
                        )
                    raise ProxyPoolUnavailableError(
                        f"PROXY_POOL_EMPTY: no healthy proxy for "
                        f"domain={domain}, "
                        f"country={effective_country or 'ANY'}, "
                        f"city={effective_city or 'ANY'}"
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
            result._tier_used = esc.tier

            # 4. Check for block.
            if result.blocked:
                outcome = "blocked"
                last_result = result
                esc.attempts_at_tier += 1

                if proxy_manager is not None and proxy is not None:
                    proxy_result_reported = await _report_proxy_attempt(
                        proxy=proxy,
                        proxy_manager=proxy_manager,
                        domain=domain,
                        success=False,
                        reason=result.block_reason or "http_error",
                        db=db,
                        engine=result.engine,
                        response_time_ms=result.elapsed_ms,
                    )
                    failed_proxy_ids.add(proxy.id)

                # Decide: escalate tier or rotate proxy?
                if esc.attempts_at_tier >= attempts_per_tier and is_escalatable(
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
                    esc.iterations_at_tier = 0
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
                        esc.iterations_at_tier = 0
                        esc.fetcher = None
                        continue
                    return result

                if total_attempts < max_attempts:
                    attempt_end = time.perf_counter()  # exclude backoff sleep
                    await asyncio.sleep(_jittered_delay(policy))
                    continue
                return result

            # 5. Success.
            outcome = "success"
            if proxy_manager is not None and proxy is not None:
                proxy_result_reported = await _report_proxy_attempt(
                    proxy=proxy,
                    proxy_manager=proxy_manager,
                    domain=domain,
                    success=True,
                    reason=None,
                    db=db,
                    engine=result.engine,
                    response_time_ms=result.elapsed_ms,
                )
            return result

        except ProxyPoolUnavailableError as exc:
            outcome = "proxy_pool_empty"
            error_type = type(exc).__name__
            error_message = str(exc)
            raise

        except ProxyPoolExhaustedError as exc:
            outcome = "proxy_pool_exhausted"
            error_type = type(exc).__name__
            error_message = str(exc)
            raise

        except asyncio.CancelledError as exc:
            outcome = "cancelled"
            error_type = type(exc).__name__
            error_message = "request attempt cancelled"
            if proxy is not None and proxy_manager is not None and not proxy_result_reported:
                try:
                    # shield(): a re-cancellation must not kill the health
                    # report mid-flight — it keeps running in the background
                    # while the cancelled task terminates.
                    proxy_result_reported = await asyncio.shield(
                        _report_proxy_attempt(
                            proxy=proxy,
                            proxy_manager=proxy_manager,
                            domain=domain,
                            success=False,
                            reason=proxy_failure_reason(exc),
                            db=db,
                            engine=attempt_engine,
                        )
                    )
                except asyncio.CancelledError:
                    pass  # the shielded report continues; the task must die
            raise

        except FetchError as exc:
            last_error = exc
            outcome = classify_fetch_error(exc)
            error_type = type(exc).__name__
            error_message = str(exc)
            if proxy is not None and proxy_manager is not None:
                if not proxy_result_reported:
                    proxy_result_reported = await _report_proxy_attempt(
                        proxy=proxy,
                        proxy_manager=proxy_manager,
                        domain=domain,
                        success=False,
                        reason=proxy_failure_reason(exc),
                        db=db,
                        engine=attempt_engine,
                    )
                failed_proxy_ids.add(proxy.id)
            if total_attempts < max_attempts:
                attempt_end = time.perf_counter()  # exclude backoff sleep
                await asyncio.sleep(_jittered_delay(policy))
                continue
            raise

        except Exception as exc:
            last_error = exc
            outcome = classify_exception(exc)
            error_type = type(exc).__name__
            error_message = str(exc)
            if proxy is not None and proxy_manager is not None:
                if not proxy_result_reported:
                    proxy_result_reported = await _report_proxy_attempt(
                        proxy=proxy,
                        proxy_manager=proxy_manager,
                        domain=domain,
                        success=False,
                        reason=proxy_failure_reason(exc),
                        db=db,
                        engine=attempt_engine,
                    )
                failed_proxy_ids.add(proxy.id)
            if total_attempts < max_attempts:
                attempt_end = time.perf_counter()  # exclude backoff sleep
                await asyncio.sleep(_jittered_delay(policy))
                continue
            raise FetchError(str(exc)) from exc

        finally:
            if attempt_recorder is not None:
                end = attempt_end if attempt_end is not None else time.perf_counter()
                duration_ms = max(0, int((end - attempt_started) * 1000))
                status_code = result.status_code if result is not None else None
                blocked = result.blocked if result is not None else False
                block_reason = result.block_reason if result is not None else None
                if result is not None:
                    raw_len = len(result.raw_body or b"")
                    bytes_received = raw_len if raw_len > 0 else len(result.body or b"")
                else:
                    bytes_received = 0
                from app.services.request_attempt_log import proxy_snapshot

                snap = proxy_snapshot(proxy)
                attempt_record = AttemptResult(
                    url=url,
                    domain=domain,
                    method="GET",
                    attempt_number=total_attempts,
                    tier_attempt_number=tier_attempt_number,
                    escalation_tier=attempt_tier,
                    proxy_id=snap.proxy_id,
                    proxy_pool_id=snap.proxy_pool_id,
                    proxy_provider=snap.provider,
                    proxy_type=snap.proxy_type,
                    proxy_country=snap.country,
                    proxy_city=snap.city,
                    engine=result.engine if result is not None else attempt_engine,
                    outcome=outcome,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    bytes_received=bytes_received,
                    blocked=blocked,
                    block_reason=block_reason,
                    error_type=error_type,
                    error=error_message,
                    requested_at=requested_at,
                    completed_at=datetime.now(UTC),
                )
                try:
                    # shield(): during cancellation a bare await here would be
                    # interrupted immediately and the attempt row would never
                    # reach PostgreSQL.  The shielded recorder keeps persisting
                    # in the background even if this task is cancelled again.
                    request_log_id = await asyncio.shield(attempt_recorder(attempt_record))
                except asyncio.CancelledError:
                    # The shielded recorder continues in the background; the
                    # cancelled task must still terminate cleanly.
                    request_log_id = None
                    if outcome != "cancelled":
                        # The cancellation did NOT originate in the try body —
                        # a successful/blocked attempt reached the finally and
                        # got cancelled while persisting.  Swallowing it here
                        # would let a task asked to die continue into WARC
                        # archival and callback delivery.  Bare raise inside
                        # the except re-raises exactly this CancelledError.
                        raise
                except Exception:
                    # A recorder bug must never hide the original exception or
                    # fail the crawl — the attempt itself already happened.
                    logger.warning("attempt_recorder_failed", exc_info=True)
                    request_log_id = None
                if result is not None and request_log_id is not None:
                    result._request_log_id = request_log_id

    # Attempt ceiling reached.  If a blocked FetchResult exists, return it —
    # the premium-gate path does the same, and a blocked final response must
    # still flow through to the caller (and to request_log).  Only raise when
    # every attempt failed with an exception.
    if last_result is not None:
        return last_result
    raise FetchError(str(last_error)) from last_error
