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

    async def read_warc_record(
        self,
        warc_filename: str,
        offset: int,
        length: int,
    ) -> bytes:
        """Fetch a byte range from a WARC file stored in S3."""
        range_header = f"bytes={offset}-{offset + length - 1}"
        try:
            response = await self._s3.get_object(
                Bucket=self._bucket,
                Key=warc_filename,
                Range=range_header,
            )
            body = await response["Body"].read()
            return body
        except Exception as exc:
            msg = str(exc)
            if "NoSuchKey" in msg:
                raise ArchiveReadError(f"WARC file not found: {warc_filename}") from exc
            raise ArchiveReadError(f"S3 error: {msg}") from exc

    async def extract_body(
        self,
        warc_filename: str,
        offset: int,
        length: int,
    ) -> tuple[bytes, str | None]:
        """Download a WARC record from S3 and extract the HTTP response body.

        Returns ``(payload_bytes, content_type)``.
        """
        import warcio

        raw = await self.read_warc_record(warc_filename, offset, length)

        try:
            # warcio.ArchiveIterator handles gzip decompression transparently.
            reader = warcio.ArchiveIterator(io.BytesIO(raw))
            for record in reader:
                if record.rec_type == "response":
                    payload = record.content_stream().read()
                    content_type = (
                        record.http_headers.get("Content-Type") if record.http_headers else None
                    )
                    return payload, content_type
                if record.rec_type == "revisit":
                    # Revisit records have no body; return empty.
                    return b"", None

            raise ArchiveReadError("No response/revisit record found in WARC range")
        except ArchiveReadError:
            raise
        except Exception as exc:
            raise ArchiveReadError(f"WARC parse error: {exc}") from exc
