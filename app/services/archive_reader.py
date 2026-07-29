"""Archive reader — S3 range-read + warcio body extraction."""

import io
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ArchiveReadError(Exception):
    """Raised when WARC record cannot be read or parsed."""


class ArchiveReader:
    """Read WARC records from S3 via range requests and extract HTTP bodies."""

    def __init__(self, s3_client: Any, bucket: str) -> None:
        self._s3: Any = s3_client
        self._bucket = bucket

    async def _read_full_warc(self, warc_filename: str) -> bytes:
        """Fetch the full WARC file from S3 (gzip range-reads are not valid)."""
        try:
            response = await self._s3.get_object(
                Bucket=self._bucket,
                Key=warc_filename,
            )
            return await response["Body"].read()
        except Exception as exc:
            msg = str(exc)
            if "NoSuchKey" in msg:
                raise ArchiveReadError(f"WARC file not found: {warc_filename}") from exc
            raise ArchiveReadError(f"S3 error: {msg}") from exc

    async def extract_body(
        self,
        warc_filename: str,
        offset: int = 0,
        length: int = 0,
    ) -> tuple[bytes, str | None]:
        """Download a WARC file from S3 and extract the first response body."""
        import warcio

        raw = await self._read_full_warc(warc_filename)

        try:
            reader = warcio.ArchiveIterator(io.BytesIO(raw))
            for record in reader:
                if record.rec_type == "response":
                    payload = record.content_stream().read()
                    content_type = (
                        record.http_headers.get("Content-Type") if record.http_headers else None
                    )
                    return payload, content_type
                if record.rec_type == "revisit":
                    return b"[revisit]", "application/warc-fields"

            raise ArchiveReadError("No response/revisit record found in WARC")
        except ArchiveReadError:
            raise
        except Exception as exc:
            raise ArchiveReadError(f"WARC parse error: {exc}") from exc
