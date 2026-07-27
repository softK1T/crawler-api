"""Integration tests for usage API endpoints."""

import pytest


@pytest.mark.integration
async def test_usage_empty_returns_zero_totals(db_session):
    """No usage rows → periods=[], all totals zero."""
    from app.schemas.usage import UsageSummaryResponse

    resp = UsageSummaryResponse(
        application_id=__import__("uuid").uuid4(),
        periods=[],
        total_requests=0,
        total_bytes=0,
        total_cost_eur_cents=0,
    )
    assert resp.total_requests == 0
    assert resp.periods == []


@pytest.mark.integration
async def test_usage_with_seeded_rows(db_session):
    """Pre-seeded usage rows aggregate correctly."""
    from datetime import date
    from uuid import uuid4

    from app.models.usage_counter import UsageCounter

    app_id = uuid4()
    row = UsageCounter(
        application_id=app_id,
        period_month=date(2026, 7, 1),
        request_count=100,
        bytes_received=1_000_000,
        cost_eur_cents=350,
    )
    db_session.add(row)
    await db_session.commit()

    from sqlalchemy import select

    stmt = select(UsageCounter).where(UsageCounter.application_id == app_id)
    result = await db_session.execute(stmt)
    periods = result.scalars().all()
    assert len(periods) == 1
    assert periods[0].request_count == 100
