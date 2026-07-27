"""Archive retrieval endpoints — WARC metadata listing and content extraction."""

import base64
import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import SCOPE_ARCHIVE, require_scope
from app.core.db import get_db
from app.models.api_key import ApiKey
from app.models.warc_index import WarcIndex
from app.schemas.archive import ArchiveContentResponse, ArchiveEntryResponse
from app.services.archive_reader import ArchiveReadError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/archive", tags=["archive"])


@router.get("/{request_id}", response_model=ArchiveContentResponse)
async def get_archive_by_request(
    request_id: UUID,
    req: Request,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ARCHIVE)),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve the full archived content for a request_id."""
    stmt = select(WarcIndex).where(WarcIndex.request_log_id == request_id).limit(1)
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Archive entry not found")

    # Resolve revisit chain: if revisit, find the original response record.
    target = row
    if row.is_revisit:
        orig_stmt = (
            select(WarcIndex)
            .where(WarcIndex.sha256 == row.sha256, WarcIndex.is_revisit.is_(False))
            .order_by(WarcIndex.captured_at.asc())
            .limit(1)
        )
        orig_result = await db.execute(orig_stmt)
        original: WarcIndex | None = orig_result.scalar_one_or_none()
        if original is None:
            raise HTTPException(
                status_code=404,
                detail="Original WARC record not found for revisit entry",
            )
        # Use original record for S3 read.
        target = original

    try:
        payload, content_type = await req.app.state.archive_reader.extract_body(
            target.warc_filename, target.offset, target.length
        )
    except ArchiveReadError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ArchiveContentResponse(
        url=row.url,
        status_code=row.status_code,
        content_type=content_type,
        body_b64=base64.b64encode(payload).decode(),
        captured_at=row.captured_at,
        warc_filename=target.warc_filename,
        is_revisit=row.is_revisit,
        sha256=row.sha256,
    )


@router.get("/", response_model=list[ArchiveEntryResponse])
async def list_archives(
    req: Request,
    url: str | None = Query(None),
    from_dt: datetime | None = Query(None, alias="from"),
    to_dt: datetime | None = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    _api_key: ApiKey = Depends(require_scope(SCOPE_ARCHIVE)),
    db: AsyncSession = Depends(get_db),
):
    """List archive entries with optional filters. Metadata only — no body bytes."""
    stmt = select(WarcIndex)
    if url is not None:
        stmt = stmt.where(WarcIndex.url == url)
    if from_dt is not None:
        stmt = stmt.where(WarcIndex.captured_at >= from_dt)
    if to_dt is not None:
        stmt = stmt.where(WarcIndex.captured_at <= to_dt)
    stmt = stmt.order_by(WarcIndex.captured_at.desc())
    stmt = stmt.limit(per_page).offset((page - 1) * per_page)

    result = await db.execute(stmt)
    return result.scalars().all()
