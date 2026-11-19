"""Durable per-attempt request/proxy observability.

One ``request_log`` row per transport attempt (one retry-loop iteration),
written through an INDEPENDENT database session and committed separately, so
a later WARC, policy-learner or usage-counter failure can never roll back the
attempt audit.  Persistence failures are logged, counted, and swallowed —
they must never fail the crawl itself.

Security invariant: proxy URLs, proxy credentials, API keys, request/response
headers, response bodies, cookies, Authorization headers and callback
secrets are never persisted or logged here.  Proxy metadata is a safe
snapshot (provider/type/country/city) taken from the ORM row.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.logging_config import get_logger

logger = logging.getLogger(__name__)
_slog = get_logger("request_attempt")

#: Error messages are truncated to this many chars before persistence.
MAX_ERROR_LENGTH = 2000
#: error_type is truncated to this many chars (DB column is VARCHAR(128)).
MAX_ERROR_TYPE_LENGTH = 128


@dataclass(frozen=True, slots=True)
class RequestAttempt:
    """Immutable snapshot of ONE transport attempt (retry-loop iteration)."""

    job_id: str
    api_key_id: UUID | None
    application_id: UUID | None
    trace_id: str | None
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


@dataclass(frozen=True, slots=True)
class ProxySnapshot:
    """Safe (URL-free) proxy metadata captured at attempt time."""

    proxy_id: UUID | None
    proxy_pool_id: UUID | None
    provider: str | None
    proxy_type: str | None
    country: str | None
    city: str | None


def proxy_snapshot(proxy: object | None) -> ProxySnapshot:
    """Build a URL-free metadata snapshot from the selected ORM proxy.

    Deliberately duck-typed (getattr only) so test doubles work alongside
    ORM rows, and deliberately does NOT copy ``proxy.url`` — proxy
    credentials live in the URL and must never reach request_log.
    """
    if proxy is None:
        return ProxySnapshot(None, None, None, None, None, None)
    proxy_type = getattr(proxy, "proxy_type", None)
    if proxy_type is None:
        proxy_type_value: str | None = None
    elif isinstance(proxy_type, str):
        proxy_type_value = proxy_type
    else:
        # StrEnum column: the string value, never the enum repr.
        proxy_type_value = getattr(proxy_type, "value", str(proxy_type))
    return ProxySnapshot(
        proxy_id=getattr(proxy, "id", None),
        proxy_pool_id=getattr(proxy, "pool_id", None),
        provider=getattr(proxy, "provider", None),
        proxy_type=proxy_type_value,
        country=getattr(proxy, "country", None),
        city=getattr(proxy, "city", None),
    )


def sanitize_error_message(message: str, limit: int = MAX_ERROR_LENGTH) -> str:
    """Truncate an exception message to a safe length for persistence."""
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


def error_class_name(exc: BaseException) -> str:
    """Return the exception class name, truncated to the column width."""
    return type(exc).__name__[:MAX_ERROR_TYPE_LENGTH]


def _attempt_log_fields(attempt: RequestAttempt) -> dict:
    """Non-secret fields for structured logging (no URL, no error message)."""
    return {
        "job_id": attempt.job_id,
        "attempt_number": attempt.attempt_number,
        "tier_attempt_number": attempt.tier_attempt_number,
        "escalation_tier": attempt.escalation_tier,
        "proxy_id": str(attempt.proxy_id) if attempt.proxy_id else None,
        "proxy_provider": attempt.proxy_provider,
        "proxy_type": attempt.proxy_type,
        "proxy_country": attempt.proxy_country,
        "domain": attempt.domain,
        "engine": attempt.engine,
        "outcome": attempt.outcome,
        "status_code": attempt.status_code,
        "blocked": attempt.blocked,
        "block_reason": attempt.block_reason,
        "duration_ms": attempt.duration_ms,
        "bytes_received": attempt.bytes_received,
        "error_type": attempt.error_type,
    }


async def persist_request_attempt(db_session_factory, attempt: RequestAttempt) -> UUID | None:
    """Persist exactly one RequestLog row in an independent session.

    Returns the row UUID after a successful commit, or None on any failure.
    Never re-raises into the fetch path — a logging failure must not fail
    the crawl.
    """
    from app.core.observability import record_attempt_metrics

    # Count the attempt even if the DB write below fails.
    record_attempt_metrics(
        outcome=attempt.outcome,
        engine=attempt.engine,
        proxied=attempt.proxy_id is not None,
        proxy_provider=attempt.proxy_provider,
        proxy_type=attempt.proxy_type,
        duration_ms=attempt.duration_ms,
    )

    try:
        from app.models.request_log import RequestLog

        async with db_session_factory() as db:
            row = RequestLog(
                # No server-side default on the partitioned composite PK —
                # the writer must generate the id itself.
                id=uuid.uuid4(),
                job_id=attempt.job_id,
                api_key_id=attempt.api_key_id,
                application_id=attempt.application_id,
                trace_id=attempt.trace_id,
                url=attempt.url,
                domain=attempt.domain,
                method=attempt.method,
                attempt_number=attempt.attempt_number,
                tier_attempt_number=attempt.tier_attempt_number,
                escalation_tier=attempt.escalation_tier,
                proxy_id=attempt.proxy_id,
                proxy_pool_id=attempt.proxy_pool_id,
                proxy_provider=attempt.proxy_provider,
                proxy_type=attempt.proxy_type,
                proxy_country=attempt.proxy_country,
                proxy_city=attempt.proxy_city,
                engine=attempt.engine,
                outcome=attempt.outcome,
                status_code=attempt.status_code,
                duration_ms=attempt.duration_ms,
                bytes_received=attempt.bytes_received,
                blocked=attempt.blocked,
                block_reason=attempt.block_reason,
                error_type=attempt.error_type,
                error=attempt.error,
                requested_at=attempt.requested_at,
                completed_at=attempt.completed_at,
            )
            db.add(row)
            try:
                await db.commit()
            except Exception:
                await db.rollback()
                raise
    except Exception:
        from app.core.observability import REQUEST_LOG_WRITE_FAILURES_TOTAL

        try:
            REQUEST_LOG_WRITE_FAILURES_TOTAL.inc()
        except Exception:  # noqa: S110 — metric layer must not fail the crawl
            pass
        logger.exception(
            "request_attempt_persist_failed",
            extra=_attempt_log_fields(attempt),
        )
        return None

    _slog.info(
        "request_attempt_recorded",
        request_log_id=str(row.id),
        **_attempt_log_fields(attempt),
    )
    return row.id
