"""Search over the fetch history kept in request_log, summarised to one row per job.

request_log holds one row per transport attempt. A job matches when:
  * domain / url filters match the job (they are the same for all its attempts);
  * engine / proxy filters match ANY of its attempts inside the window;
  * outcome / blocked / block_reason / status_code match its LAST attempt.
Every query is bounded in time (request_log is partitioned by requested_at) and
scoped to the calling application.
"""

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.request_log import REQUEST_LOG_OUTCOMES, RequestLog
from app.services.policy_resolver import normalize_domain

DEFAULT_WINDOW = timedelta(hours=24)
MAX_WINDOW = timedelta(days=31)

SUMMARY_COLUMNS: tuple[str, ...] = (
    "job_id",
    "url",
    "domain",
    "engine",
    "outcome",
    "status_code",
    "blocked",
    "block_reason",
    "error_type",
    "attempts",
    "duration_ms",
    "started_at",
    "finished_at",
)


class FetchFilterError(ValueError):
    """A search parameter is malformed; the endpoint turns this into a 422."""


@dataclass(frozen=True, slots=True)
class FetchFilters:
    since: datetime
    until: datetime
    domains: tuple[str, ...] = ()
    url: str | None = None
    url_prefix: str | None = None
    engines: tuple[str, ...] = ()
    proxy_countries: tuple[str, ...] = ()
    proxy_types: tuple[str, ...] = ()
    outcomes: tuple[str, ...] = ()
    block_reasons: tuple[str, ...] = ()
    status_codes: tuple[int, ...] = ()
    blocked: bool | None = None
    min_duration_ms: int | None = None


def split_values(raw: str | None) -> tuple[str, ...]:
    """Split a comma-separated query value, dropping blanks and duplicates."""
    if not raw:
        return ()
    return tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def resolve_window(
    since: datetime | None, until: datetime | None, *, now: datetime | None = None
) -> tuple[datetime, datetime]:
    """Return the half-open [since, until) window in UTC; default is the last 24 hours."""
    end = _utc(until) if until is not None else (now or datetime.now(UTC))
    start = _utc(since) if since is not None else end - DEFAULT_WINDOW
    if start >= end:
        raise FetchFilterError("'from' must be earlier than 'to'")
    if end - start > MAX_WINDOW:
        raise FetchFilterError(f"the search window is limited to {MAX_WINDOW.days} days")
    return start, end


def domain_candidates(raw: str | None) -> tuple[str, ...]:
    """Each requested domain as typed and in the normalized form request_log stores."""
    found: dict[str, None] = {}
    for value in split_values(raw):
        lowered = value.lower()
        found[lowered] = None
        normalized = normalize_domain(lowered)
        if normalized:
            found[normalized] = None
    return tuple(found)


def parse_outcomes(raw: str | None) -> tuple[str, ...]:
    values = split_values(raw)
    unknown = [value for value in values if value not in REQUEST_LOG_OUTCOMES]
    if unknown:
        raise FetchFilterError(
            f"unknown outcome {unknown[0]!r}; allowed: {', '.join(REQUEST_LOG_OUTCOMES)}"
        )
    return values


def parse_status_codes(raw: str | None) -> tuple[int, ...]:
    try:
        return tuple(int(value) for value in split_values(raw))
    except ValueError:
        raise FetchFilterError("status_code must be a comma-separated list of integers") from None


def encode_cursor(started_at: datetime, job_id: str) -> str:
    return base64.urlsafe_b64encode(f"{started_at.isoformat()}|{job_id}".encode()).decode()


def decode_cursor(raw: str) -> tuple[datetime, str]:
    try:
        stamp, separator, job_id = base64.urlsafe_b64decode(raw.encode()).decode().partition("|")
        if not separator or not job_id:
            raise ValueError(raw)
        return _utc(datetime.fromisoformat(stamp)), job_id
    except (ValueError, binascii.Error):
        raise FetchFilterError("invalid cursor") from None


def build_search(
    application_id: UUID,
    filters: FetchFilters,
    *,
    limit: int,
    cursor: tuple[datetime, str] | None = None,
) -> Select[Any]:
    """Newest-first job summaries; fetches limit + 1 rows so the caller can tell if more exist."""
    log = RequestLog
    job = log.job_id

    columns: list[ColumnElement[Any]] = [
        job.label("job_id"),
        log.url.label("url"),
        log.domain.label("domain"),
        log.engine.label("engine"),
        log.outcome.label("outcome"),
        log.status_code.label("status_code"),
        log.blocked.label("blocked"),
        log.block_reason.label("block_reason"),
        log.error_type.label("error_type"),
        func.row_number()
        .over(partition_by=job, order_by=(log.attempt_number.desc(), log.requested_at.desc()))
        .label("rn"),
        func.count().over(partition_by=job).label("attempts"),
        func.sum(func.coalesce(log.duration_ms, 0)).over(partition_by=job).label("duration_ms"),
        func.min(log.requested_at).over(partition_by=job).label("started_at"),
        func.max(func.coalesce(log.completed_at, log.requested_at))
        .over(partition_by=job)
        .label("finished_at"),
    ]

    hits: list[str] = []

    def any_attempt(name: str, condition: ColumnElement[bool]) -> None:
        columns.append(func.max(case((condition, 1), else_=0)).over(partition_by=job).label(name))
        hits.append(name)

    if filters.engines:
        any_attempt("engine_hit", log.engine.in_(filters.engines))
    if filters.proxy_countries:
        any_attempt("country_hit", log.proxy_country.in_(filters.proxy_countries))
    if filters.proxy_types:
        any_attempt("type_hit", log.proxy_type.in_(filters.proxy_types))

    scope: list[ColumnElement[bool]] = [
        log.application_id == application_id,
        job.is_not(None),
        log.requested_at >= filters.since,
        log.requested_at < filters.until,
    ]
    if filters.domains:
        scope.append(log.domain.in_(filters.domains))
    if filters.url:
        scope.append(log.url == filters.url)
    if filters.url_prefix:
        scope.append(log.url.startswith(filters.url_prefix, autoescape=True))

    last = select(*columns).where(*scope).subquery("attempts").c

    final: list[ColumnElement[bool]] = [last.rn == 1]
    final.extend(last[name] == 1 for name in hits)
    if filters.outcomes:
        final.append(last.outcome.in_(filters.outcomes))
    if filters.block_reasons:
        final.append(last.block_reason.in_(filters.block_reasons))
    if filters.status_codes:
        final.append(last.status_code.in_(filters.status_codes))
    if filters.blocked is not None:
        final.append(last.blocked.is_(filters.blocked))
    if filters.min_duration_ms is not None:
        final.append(last.duration_ms >= filters.min_duration_ms)
    if cursor is not None:
        started_at, job_id = cursor
        final.append(
            or_(
                last.started_at < started_at,
                and_(last.started_at == started_at, last.job_id < job_id),
            )
        )

    return (
        select(*(last[name] for name in SUMMARY_COLUMNS))
        .where(*final)
        .order_by(last.started_at.desc(), last.job_id.desc())
        .limit(limit + 1)
    )


async def search_fetches(
    db: AsyncSession,
    application_id: UUID,
    filters: FetchFilters,
    *,
    limit: int,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Return up to *limit* job summaries and the cursor for the next page, if any."""
    position = decode_cursor(cursor) if cursor else None
    result = await db.execute(build_search(application_id, filters, limit=limit, cursor=position))
    rows = [dict(row) for row in result.mappings().all()]
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1]["started_at"], rows[-1]["job_id"])
    return rows, next_cursor


async def get_fetch_attempts(
    db: AsyncSession, application_id: UUID, job_id: str
) -> list[RequestLog]:
    """All recorded attempts of one job, oldest first; empty when it is not the caller's job."""
    result = await db.execute(
        select(RequestLog)
        .where(RequestLog.job_id == job_id, RequestLog.application_id == application_id)
        .order_by(RequestLog.attempt_number, RequestLog.requested_at)
    )
    return list(result.scalars().all())
