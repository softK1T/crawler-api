"""Usage statistics endpoints — per-application and admin views."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import SCOPE_ADMIN, require_scope, resolve_api_key
from app.core.db import get_db
from app.models.api_key import ApiKey
from app.models.usage_counter import UsageCounter
from app.schemas.usage import UsagePeriodResponse, UsageSummaryResponse

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/", response_model=UsageSummaryResponse)
async def get_my_usage(
    api_key: ApiKey = Depends(resolve_api_key),
    db: AsyncSession = Depends(get_db),
    from_month: date | None = Query(None),
    to_month: date | None = Query(None),
):
    """Get usage statistics for the caller's application."""
    return await _build_summary(api_key.application_id, from_month, to_month, db)


@router.get("/applications/{application_id}", response_model=UsageSummaryResponse)
async def get_app_usage(
    application_id: UUID,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
    from_month: date | None = Query(None),
    to_month: date | None = Query(None),
):
    """Admin: get usage statistics for any application."""
    return await _build_summary(application_id, from_month, to_month, db)


async def _build_summary(
    application_id: UUID,
    from_month: date | None,
    to_month: date | None,
    db: AsyncSession,
) -> UsageSummaryResponse:
    stmt = select(UsageCounter).where(UsageCounter.application_id == application_id)
    if from_month is not None:
        stmt = stmt.where(UsageCounter.period_month >= from_month)
    if to_month is not None:
        stmt = stmt.where(UsageCounter.period_month <= to_month)
    stmt = stmt.order_by(UsageCounter.period_month.desc())

    result = await db.execute(stmt)
    periods = result.scalars().all()

    total_requests = sum(p.request_count for p in periods)
    total_bytes = sum(p.bytes_received for p in periods)
    total_cost = sum(p.cost_eur_cents for p in periods)

    return UsageSummaryResponse(
        application_id=application_id,
        periods=[UsagePeriodResponse.model_validate(p) for p in periods],
        total_requests=total_requests,
        total_bytes=total_bytes,
        total_cost_eur_cents=total_cost,
    )
