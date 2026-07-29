"""WARC dead-letter queue — local disk buffer for failed S3 uploads.

When an S3 upload fails, the gzipped WARC buffer and its metadata are persisted
to a local directory.  An arq cron task retries uploads every 15 minutes.
"""

import gzip
import json
import logging
import os
from datetime import UTC, datetime

from app.core.config import settings

logger = logging.getLogger(__name__)

DLQ_META_SUFFIX = ".meta.json"


class WarcDLQ:
    """Bounded on-disk queue for WARC files that failed S3 upload."""

    def __init__(self) -> None:
        self._dir = settings.warc_dlq_dir
        self._max_bytes = settings.warc_dlq_max_bytes

    # ── write ──────────────────────────────────────────────────────────────

    async def store(self, filename: str, data: bytes) -> bool:
        """Persist a failed upload to disk.  Returns True on success."""
        try:
            os.makedirs(self._dir, exist_ok=True)
        except OSError as exc:
            logger.error("WARC DLQ: cannot create directory %s: %s", self._dir, exc)
            return False

        # Enforce size cap by removing oldest entries.
        await self._enforce_cap(len(data))

        base = os.path.join(self._dir, filename.replace("/", "_"))
        try:
            with gzip.open(base, "wb") as fh:
                fh.write(data)
            meta = {"filename": filename, "stored_at": datetime.now(UTC).isoformat()}
            with open(base + DLQ_META_SUFFIX, "w") as fh:
                json.dump(meta, fh)
            logger.info("WARC DLQ: stored %s (%d bytes)", filename, len(data))
            return True
        except OSError as exc:
            logger.error("WARC DLQ: write failed for %s: %s", filename, exc)
            return False

    # ── retry ──────────────────────────────────────────────────────────────

    async def retry_uploads(self, s3_client, bucket: str) -> int:
        """Attempt re-upload of all DLQ entries.  Returns count of successes."""
        if not os.path.isdir(self._dir):
            return 0

        succeeded = 0
        for entry in os.listdir(self._dir):
            if entry.endswith(DLQ_META_SUFFIX):
                continue
            path = os.path.join(self._dir, entry)
            meta_path = path + DLQ_META_SUFFIX
            try:
                with open(meta_path) as fh:
                    meta = json.load(fh)
                with gzip.open(path, "rb") as fh:
                    data = fh.read()
                await s3_client.put_object(
                    Bucket=bucket,
                    Key=meta["filename"],
                    Body=data,
                    ContentType="application/warc",
                )
                os.remove(path)
                os.remove(meta_path)
                succeeded += 1
                logger.info("WARC DLQ: re-uploaded %s", meta["filename"])
            except Exception as exc:
                logger.warning("WARC DLQ: retry failed for %s: %s", entry, exc)
        return succeeded

    # ── gauge ──────────────────────────────────────────────────────────────

    def count(self) -> int:
        """Return the number of entries currently in the DLQ."""
        if not os.path.isdir(self._dir):
            return 0
        return sum(1 for e in os.listdir(self._dir) if not e.endswith(DLQ_META_SUFFIX))

    # ── internal ───────────────────────────────────────────────────────────

    async def _enforce_cap(self, incoming_bytes: int) -> None:
        """Remove oldest entries until total size + incoming fits under cap."""
        entries = []
        total = 0
        for entry in os.listdir(self._dir):
            if entry.endswith(DLQ_META_SUFFIX):
                continue
            path = os.path.join(self._dir, entry)
            try:
                st = os.stat(path)
                entries.append((st.st_mtime, st.st_size, path))
                total += st.st_size
            except OSError:
                pass

        entries.sort()  # oldest first by mtime
        while total + incoming_bytes > self._max_bytes and entries:
            _mtime, sz, path = entries.pop(0)
            try:
                os.remove(path)
                meta = path + DLQ_META_SUFFIX
                if os.path.exists(meta):
                    os.remove(meta)
                total -= sz
                logger.warning("WARC DLQ: evicted oldest entry %s", os.path.basename(path))
            except OSError:
                pass


# Singleton for app-level and worker-level access.
_dlq: WarcDLQ | None = None


def get_dlq() -> WarcDLQ:
    global _dlq
    if _dlq is None:
        _dlq = WarcDLQ()
    return _dlq
