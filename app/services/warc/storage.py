"""WARC storage — S3/MinIO upload and index orchestration."""

import hashlib
import logging
import secrets
from datetime import UTC, datetime
from uuid import UUID

logger = logging.getLogger(__name__)


class WarcStorage:
    """Orchestrates WARC writes, dedup, S3 upload, and WarcIndex persistence.

    The internal :class:`WarcWriter` is NOT thread-safe — all access must
    come from the single event-loop thread.
    """

    def __init__(self, writer, db_session_factory, s3_client, bucket: str) -> None:
        self._writer = writer
        self._db_factory = db_session_factory
        self._s3 = s3_client
        self._bucket = bucket

    async def archive(
        self,
        *,
        fetch_result,
        request_log_id: UUID | None,
        db,
    ) -> None:
        """Archive a successful fetch: dedup → write → maybe rotate → index."""
        from app.services.warc.dedup import check_duplicate, index_record

        sha256_digest = hashlib.sha256(fetch_result.body).hexdigest()
        content_type = fetch_result.headers.get("content-type", "application/octet-stream")

        # 1. Dedup check.
        existing = await check_duplicate(fetch_result.url, sha256_digest, db)

        if existing is not None:
            warc_record = self._writer.write_revisit(
                url=fetch_result.url,
                original_record_id=existing.warc_filename,
                original_sha256=existing.sha256,
            )
        else:
            warc_record = self._writer.write_response(
                url=fetch_result.url,
                status_code=fetch_result.status_code,
                http_headers=fetch_result.headers,
                body=fetch_result.body,
                content_type=content_type,
            )

        # 2. Rotate if needed — capture the filename BEFORE rotation so the
        #    index row matches the file actually uploaded to S3.
        warc_filename = self._writer.filename
        if self._writer.needs_rotation():
            await self._rotate()

        # 3. Index.
        warc_index = await index_record(
            warc_record,
            warc_filename,
            request_log_id,
            fetch_result.status_code,
            content_type,
            db,
        )
        return warc_index

    async def _rotate(self) -> None:
        """Upload current WARC buffer to S3 and reset the writer.

        If S3 is not configured (no access key), the buffer is discarded with
        a warning.  Upload failure also discards (known limitation — ADR-008).
        """
        from app.core.config import settings

        data = self._writer.get_bytes()

        if settings.s3_access_key:
            try:
                await self._s3.put_object(
                    Bucket=self._bucket,
                    Key=self._writer.filename,
                    Body=data,
                    ContentType="application/warc",
                )
                logger.info("Uploaded WARC: %s (%d bytes)", self._writer.filename, len(data))
            except Exception:
                logger.error(
                    "S3 upload failed for %s — WARC data discarded",
                    self._writer.filename,
                    exc_info=True,
                )
        else:
            logger.warning(
                "S3 not configured — WARC data discarded on rotation (%s, %d bytes)",
                self._writer.filename,
                len(data),
            )

        # Reset writer for next rotation window.
        from app.services.warc.writer import WarcWriter

        self._writer = WarcWriter(
            filename=_new_filename(settings.warc_prefix),
            max_size_bytes=settings.warc_max_size_bytes,
            max_age_s=settings.warc_max_age_s,
        )

    async def shutdown_flush(self) -> None:
        """Flush the current WARC buffer on graceful shutdown (no DB access)."""
        await self._rotate()


def _new_filename(prefix: str = "warc") -> str:
    """Generate a timestamped WARC filename with random suffix to avoid collisions."""
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    suffix = secrets.token_hex(2)
    return f"{prefix}/{datetime.now(UTC).strftime('%Y/%m/%d')}/crawl-{ts}-{suffix}Z.warc.gz"


async def create_warc_storage(settings) -> "WarcStorage":
    """Factory — creates the S3 client, ensures the bucket exists, and
    returns a ready-to-use WarcStorage.
    """
    import aioboto3

    from app.core.db import AsyncSessionLocal
    from app.services.warc.writer import WarcWriter

    session = aioboto3.Session()
    s3_client = await session.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    ).__aenter__()

    # Ensure the WARC bucket exists (idempotent, with retries).
    import asyncio

    for attempt in range(5):
        try:
            await s3_client.create_bucket(Bucket=settings.s3_bucket)
            break
        except Exception:
            if attempt < 4:
                await asyncio.sleep(1)
            # BucketAlreadyExists / BucketAlreadyOwnedByYou — fine.

    writer = WarcWriter(
        filename=_new_filename(settings.warc_prefix),
        max_size_bytes=settings.warc_max_size_bytes,
        max_age_s=settings.warc_max_age_s,
    )

    return WarcStorage(
        writer=writer,
        db_session_factory=AsyncSessionLocal,
        s3_client=s3_client,
        bucket=settings.s3_bucket,
    )
