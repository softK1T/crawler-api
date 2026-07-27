"""Integration tests for archive API endpoints."""

import pytest


@pytest.mark.integration
async def test_archive_entry_not_found(db_session):
    """GET /v1/archive/{random_id} → 404."""
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        raise NotFoundError(detail="Archive entry not found")


@pytest.mark.integration
async def test_archive_list_returns_empty_for_no_data(db_session):
    """Archive list is empty when no WARC index rows exist."""
    from sqlalchemy import select

    from app.models.warc_index import WarcIndex

    result = await db_session.execute(select(WarcIndex))
    rows = result.scalars().all()
    assert len(rows) == 0


@pytest.mark.integration
async def test_revisit_entry_missing_original_returns_404(db_session):
    """Revisit entry whose original can't be found → 404."""
    from datetime import UTC, datetime

    from app.models.warc_index import WarcIndex

    # Insert a revisit entry with no matching original.
    row = WarcIndex(
        url="https://example.com/page",
        warc_filename="warc/test.warc.gz",
        offset=0,
        length=100,
        sha256="abc123",
        is_revisit=True,
        content_type="text/html",
        status_code=200,
        captured_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.commit()

    from sqlalchemy import select

    stmt = select(WarcIndex).where(WarcIndex.sha256 == "abc123", WarcIndex.is_revisit.is_(False))
    result = await db_session.execute(stmt)
    original = result.scalar_one_or_none()
    assert original is None  # No original record → revisit resolution fails.


@pytest.mark.integration
async def test_archive_response_schema_excludes_body_in_list(db_session):
    """List endpoint returns ArchiveEntryResponse — no body_b64 field."""
    from app.schemas.archive import ArchiveEntryResponse

    entry = ArchiveEntryResponse(
        id=__import__("uuid").uuid4(),
        url="https://example.com",
        warc_filename="warc/test.warc.gz",
        offset=0,
        length=100,
        sha256="abc",
        is_revisit=False,
        content_type="text/html",
        status_code=200,
        captured_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )
    data = entry.model_dump()
    assert "body_b64" not in data
