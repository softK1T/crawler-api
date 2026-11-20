"""Fetch history endpoints — search past fetch jobs and inspect their attempts."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import SCOPE_FETCH, require_scope
from app.core.db import get_db
from app.models.api_key import ApiKey
from app.schemas.fetches import (
    FetchAttempt,
    FetchDetailResponse,
    FetchListResponse,
    FetchSummary,
)
from app.services.fetch_history import (
    FetchFilterError,
    FetchFilters,
    domain_candidates,
    get_fetch_attempts,
    parse_outcomes,
    parse_status_codes,
    resolve_window,
    search_fetches,
    split_values,
)

router = APIRouter(prefix="/v1/fetches", tags=["fetches"])

_LIST = "Several values may be given separated by commas."


@router.get("", response_model=FetchListResponse)
async def list_fetches(
    domain: str | None = Query(None, description=f"Site domain. {_LIST}"),
    url: str | None = Query(None, description="Exact URL."),
    url_prefix: str | None = Query(None, description="URL prefix."),
    from_dt: datetime | None = Query(
        None, alias="from", description="Window start, inclusive. Default: 24 hours before 'to'."
    ),
    to_dt: datetime | None = Query(
        None, alias="to", description="Window end, exclusive. Default: now."
    ),
    engine: str | None = Query(None, description=f"Engine used by any attempt. {_LIST}"),
    proxy_country: str | None = Query(None, description=f"Proxy country of any attempt. {_LIST}"),
    proxy_type: str | None = Query(None, description=f"Proxy type of any attempt. {_LIST}"),
    outcome: str | None = Query(None, description=f"Outcome of the last attempt. {_LIST}"),
    blocked: bool | None = Query(None, description="Whether the last attempt was blocked."),
    block_reason: str | None = Query(
        None, description=f"Block reason of the last attempt. {_LIST}"
    ),
    status_code: str | None = Query(None, description=f"Status code of the last attempt. {_LIST}"),
    min_duration_ms: int | None = Query(None, ge=0, description="Minimum total duration."),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, description="next_cursor from the previous page."),
    api_key: ApiKey = Depends(require_scope(SCOPE_FETCH)),
    db: AsyncSession = Depends(get_db),
) -> FetchListResponse:
    """Search the caller's fetch history: one row per job, newest first."""
    try:
        since, until = resolve_window(from_dt, to_dt)
        filters = FetchFilters(
            since=since,
            until=until,
            domains=domain_candidates(domain),
            url=url,
            url_prefix=url_prefix,
            engines=tuple(value.lower() for value in split_values(engine)),
            proxy_countries=tuple(value.upper() for value in split_values(proxy_country)),
            proxy_types=tuple(value.lower() for value in split_values(proxy_type)),
            outcomes=parse_outcomes(outcome),
            block_reasons=split_values(block_reason),
            status_codes=parse_status_codes(status_code),
            blocked=blocked,
            min_duration_ms=min_duration_ms,
        )
        rows, next_cursor = await search_fetches(
            db, api_key.application_id, filters, limit=limit, cursor=cursor
        )
    except FetchFilterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return FetchListResponse(
        items=[FetchSummary.model_validate(row) for row in rows],
        next_cursor=next_cursor,
        window_from=since,
        window_to=until,
    )


@router.get("/{job_id}", response_model=FetchDetailResponse)
async def get_fetch(
    job_id: str,
    api_key: ApiKey = Depends(require_scope(SCOPE_FETCH)),
    db: AsyncSession = Depends(get_db),
) -> FetchDetailResponse:
    """Every recorded attempt of one of the caller's fetch jobs."""
    attempts = await get_fetch_attempts(db, api_key.application_id, job_id)
    if not attempts:
        raise HTTPException(status_code=404, detail="Fetch not found")
    last = attempts[-1]
    return FetchDetailResponse(
        job_id=job_id,
        url=last.url,
        domain=last.domain,
        attempts=[FetchAttempt.model_validate(attempt) for attempt in attempts],
    )
