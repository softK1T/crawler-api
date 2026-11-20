"""Runs the fetch-history SQL on real Postgres with every filter switched on."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.fetch_history import (
    FetchFilters,
    encode_cursor,
    get_fetch_attempts,
    search_fetches,
)


@pytest.mark.integration
async def test_search_executes_and_never_leaks_other_applications(db_session: AsyncSession) -> None:
    until = datetime.now(UTC)
    filters = FetchFilters(
        since=until - timedelta(days=1),
        until=until,
        domains=("example.com",),
        url_prefix="https://example.com/",
        engines=("camoufox",),
        proxy_countries=("PL",),
        proxy_types=("datacenter",),
        outcomes=("blocked",),
        block_reasons=("cloudflare",),
        status_codes=(403,),
        blocked=True,
        min_duration_ms=1,
    )
    stranger = uuid.uuid4()

    rows, next_cursor = await search_fetches(
        db_session, stranger, filters, limit=5, cursor=encode_cursor(until, "job")
    )
    assert rows == []
    assert next_cursor is None

    everything = FetchFilters(since=until - timedelta(days=31), until=until)
    assert await search_fetches(db_session, stranger, everything, limit=5) == ([], None)
    assert await get_fetch_attempts(db_session, stranger, "job-missing") == []
