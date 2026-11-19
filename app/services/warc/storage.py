"""Durable WARC write-ahead spool, S3 delivery and idempotent index recovery.

Each capture is a complete response object, not an in-memory rotation window.
No revisit records are emitted: every new archive is independently readable.
"""

import asyncio
import base64
import fcntl
import hashlib
import importlib
import io
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


class WarcSpool:
    """Atomic, fsynced local journal. Full means backpressure, never eviction."""

    def __init__(self, directory: str, max_bytes: int) -> None:
        self.directory = Path(directory)
        self.max_bytes = max_bytes

    def pending(self) -> list[Path]:
        return sorted(self.directory.glob("*.json"))

    def stage(self, bucket: str, index: dict, body: bytes) -> Path:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "bucket": bucket,
                "index": index,
                "body": base64.b64encode(body).decode("ascii"),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        ).encode()
        with (self.directory / ".lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            used = sum(p.stat().st_size for p in self.pending())
            if used + len(payload) > self.max_bytes:
                raise OSError("WARC spool full; refusing capture without deleting pending archives")
            destination = self.directory / f"{index['id']}.json"
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=self.directory, suffix=".tmp", delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
                self._sync()
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return destination

    def read(self, path: Path) -> tuple[dict, bytes]:
        entry = json.loads(path.read_bytes())
        body = base64.b64decode(entry["body"], validate=True)
        if hashlib.sha256(body).hexdigest() != entry["sha256"]:
            raise ValueError("WARC spool checksum mismatch; entry retained for operator recovery")
        return entry, body

    def remove(self, path: Path) -> None:
        path.unlink(missing_ok=True)
        self._sync()

    def _sync(self) -> None:
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _capture(fetch_result, request_log_id, warc_body):
    from warcio.statusandheaders import StatusAndHeaders
    from warcio.warcwriter import WARCWriter

    from app.core.config import settings

    body = warc_body if warc_body is not None else fetch_result.body
    headers = getattr(fetch_result, "raw_headers", None) if warc_body is not None else None
    headers = headers or fetch_result.headers
    # Stored transport bytes are not HTTP chunk framing.
    headers = {k: v for k, v in headers.items() if k.lower() != "transfer-encoding"}
    headers = {k: v for k, v in headers.items() if k.lower() != "content-length"}
    headers["Content-Length"] = str(len(body))
    content_type = next(
        (v for k, v in headers.items() if k.lower() == "content-type"), "application/octet-stream"
    )
    buffer = io.BytesIO()
    writer = WARCWriter(buffer, gzip=True)
    record = writer.create_warc_record(
        fetch_result.url,
        "response",
        payload=io.BytesIO(body),
        http_headers=StatusAndHeaders(
            f"{fetch_result.status_code} OK", list(headers.items()), protocol="HTTP/1.1"
        ),
    )
    writer.write_record(record)
    data = buffer.getvalue()
    now = datetime.now(UTC)
    index = {
        "id": str(uuid4()),
        "request_log_id": str(request_log_id) if request_log_id else None,
        "url": fetch_result.url,
        "warc_filename": _new_filename(settings.warc_prefix),
        "offset": 0,
        "length": len(data),
        "sha256": hashlib.sha256(body).hexdigest(),
        "is_revisit": False,
        "content_type": content_type,
        "status_code": fetch_result.status_code,
        "captured_at": now.isoformat(),
    }
    return index, data


async def _persist_index(db, metadata: dict):
    from sqlalchemy.dialects.postgresql import insert

    from app.models.warc_index import WarcIndex

    values = dict(metadata)
    values["id"] = UUID(values["id"])
    values["request_log_id"] = UUID(values["request_log_id"]) if values["request_log_id"] else None
    values["captured_at"] = datetime.fromisoformat(values["captured_at"])
    await db.execute(
        insert(WarcIndex).values(**values).on_conflict_do_nothing(index_elements=["id"])
    )
    await db.commit()
    row = await db.get(WarcIndex, values["id"])
    if row is None:
        raise RuntimeError("WARC index persistence could not be verified")
    return row


class WarcStorage:
    def __init__(
        self, writer, db_session_factory, s3_client, bucket: str, *, spool=None, client_context=None
    ) -> None:
        from app.core.config import settings

        self._db_factory = db_session_factory
        self._s3 = s3_client
        self._bucket = bucket
        self._spool = spool or WarcSpool(settings.warc_dlq_dir, settings.warc_dlq_max_bytes)
        self._client_context = client_context
        self._lock = asyncio.Lock()

    async def archive(self, *, fetch_result, request_log_id, db, warc_body=None):
        index, data = _capture(fetch_result, request_log_id, warc_body)
        async with self._lock:
            path = await asyncio.to_thread(self._spool.stage, self._bucket, index, data)
            # Cancellation or any failure leaves the journal for cron/startup recovery.
            await self._s3.put_object(
                Bucket=self._bucket,
                Key=index["warc_filename"],
                Body=data,
                ContentType="application/warc",
            )
            row = await _persist_index(db, index)
            await asyncio.to_thread(self._spool.remove, path)
            return row

    async def retry_uploads(self) -> int:
        succeeded = 0
        async with self._lock:
            paths = await asyncio.to_thread(self._spool.pending)
            for path in paths[:10]:
                try:
                    entry, body = await asyncio.to_thread(self._spool.read, path)
                    await self._s3.put_object(
                        Bucket=entry["bucket"],
                        Key=entry["index"]["warc_filename"],
                        Body=body,
                        ContentType="application/warc",
                    )
                    async with self._db_factory() as db:
                        await _persist_index(db, entry["index"])
                    await asyncio.to_thread(self._spool.remove, path)
                    succeeded += 1
                except Exception:
                    logger.exception("WARC replay deferred; journal retained: %s", path.name)
                    break
        return succeeded

    async def shutdown_flush(self) -> None:
        # All captures are already journalled; there is no volatile rotation buffer.
        if self._client_context is not None:
            await self._client_context.__aexit__(None, None, None)
            self._client_context = None


def _new_filename(prefix: str = "warc") -> str:
    now = datetime.now(UTC)
    return f"{prefix}/{now:%Y/%m/%d}/crawl-{now:%Y%m%dT%H%M%S}-{uuid4().hex}.warc.gz"


async def retry_warc_uploads(ctx: dict) -> None:
    storage = ctx.get("warc_storage")
    if storage is not None:
        await storage.retry_uploads()


async def create_warc_storage(settings) -> WarcStorage:
    import aioboto3

    from app.core.db import AsyncSessionLocal

    Config = importlib.import_module("botocore.config").Config
    ClientError = importlib.import_module("botocore.exceptions").ClientError
    if not settings.s3_access_key or not settings.s3_secret_key:
        raise RuntimeError("WARC enabled without S3 credentials")
    context = aioboto3.Session().client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=10,
            retries={"max_attempts": 1},
        ),
    )
    client = await context.__aenter__()
    try:
        try:
            await client.head_bucket(Bucket=settings.s3_bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            options: dict = {"Bucket": settings.s3_bucket}
            if settings.s3_region != "us-east-1":
                options["CreateBucketConfiguration"] = {"LocationConstraint": settings.s3_region}
            try:
                await client.create_bucket(**options)
            except ClientError as create_exc:
                if create_exc.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
                    raise
        await client.head_bucket(Bucket=settings.s3_bucket)
    except BaseException:
        await context.__aexit__(None, None, None)
        raise
    return WarcStorage(None, AsyncSessionLocal, client, settings.s3_bucket, client_context=context)
