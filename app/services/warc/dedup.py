"""SHA-256 deduplication and WarcIndex persistence."""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def check_duplicate(
    url: str,
    sha256: str,
    db: AsyncSession,
) -> None:
    """Return an existing WarcIndex row if (url, sha256) pair was already captured.

    Returns ``None`` on DB errors (safe fallback — writes a full response record).
    """
    from app.models.warc_index import WarcIndex

    try:
        stmt = (
            select(WarcIndex)
            .where(WarcIndex.sha256 == sha256, WarcIndex.url == url)
            .order_by(WarcIndex.captured_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    except Exception:
        logger.warning("Dedup check failed for url=%s — falling through to full record", url)
        return None


async def index_record(
    warc_record,
    warc_filename: str,
    request_log_id: UUID | None,
    status_code: int,
    content_type: str | None,
    db: AsyncSession,
) -> None:
    """Insert a WarcIndex row and return it."""
    from app.models.warc_index import WarcIndex

    row = WarcIndex(
        request_log_id=request_log_id,
        url=warc_record.target_uri,
        warc_filename=warc_filename,
        offset=warc_record.offset,
        length=warc_record.length,
        sha256=warc_record.sha256,
        is_revisit=(warc_record.warc_type == "revisit"),
        content_type=content_type,
        status_code=status_code,
        captured_at=warc_record.date,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row
