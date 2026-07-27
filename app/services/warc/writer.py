"""In-process WARC file builder using warcio + in-memory BytesIO buffer.

WarcWriter is NOT thread-safe — it lives on ``app.state.warc_storage``
and must only be accessed from the async event-loop thread.
"""

import hashlib
import io
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime

WARC_VERSION = "WARC/1.1"


@dataclass
class WarcRecord:
    warc_type: str  # "response" | "revisit"
    target_uri: str
    date: datetime
    content_type: str
    payload: bytes
    headers: dict[str, str] = field(default_factory=dict)
    warc_record_id: str = ""
    refers_to: str | None = None
    sha256: str = ""
    offset: int = 0
    length: int = 0


class WarcWriter:
    """Accumulates WARC records into an in-memory gzip-compressed buffer.

    Call :meth:`needs_rotation` after each write and trigger rotation
    (upload + reset) when it returns ``True``.
    """

    def __init__(
        self,
        filename: str,
        max_size_bytes: int,
        max_age_s: int,
    ) -> None:
        import warcio

        self._buf = io.BytesIO()
        self._writer = warcio.WARCWriter(self._buf, gzip=True)
        self._created_at = datetime.now(UTC)
        self._filename = filename
        self._max_size_bytes = max_size_bytes
        self._max_age_s = max_age_s
        self._records: list[WarcRecord] = []

    # ── properties ────────────────────────────────────────────────────────────

    @property
    def filename(self) -> str:
        return self._filename

    @property
    def records(self) -> list[WarcRecord]:
        return list(self._records)

    # ── write helpers ─────────────────────────────────────────────────────────

    def write_response(
        self,
        url: str,
        status_code: int,
        http_headers: dict[str, str],
        body: bytes,
        content_type: str,
        date: datetime | None = None,
    ) -> WarcRecord:
        """Write a full WARC response record.  Returns offset/length metadata."""
        import warcio.statusandheaders

        now = date or datetime.now(UTC)
        record_id = f"<urn:uuid:{secrets.token_hex(16)}>"
        sha256_digest = hashlib.sha256(body).hexdigest()

        status_line = f"{status_code} OK"
        http_status = warcio.statusandheaders.StatusAndHeaders(
            status_line, list(http_headers.items()), protocol="HTTP/1.1"
        )

        offset = self._buf.tell()
        self._writer.write_record(
            warcio.WARCRecord(
                type="response",
                uri=url,
                date=now.isoformat(),
                payload=io.BytesIO(body),
                http_headers=http_status,
                warc_headers_dict={
                    "WARC-Record-ID": record_id,
                    "Content-Type": content_type,
                    "WARC-Block-Digest": f"sha256:{sha256_digest}",
                },
            )
        )
        length = self._buf.tell() - offset

        rec = WarcRecord(
            warc_type="response",
            target_uri=url,
            date=now,
            content_type=content_type,
            payload=body,
            headers=http_headers,
            warc_record_id=record_id,
            sha256=sha256_digest,
            offset=offset,
            length=length,
        )
        self._records.append(rec)
        return rec

    def write_revisit(
        self,
        url: str,
        original_record_id: str,
        original_sha256: str,
        date: datetime | None = None,
    ) -> WarcRecord:
        """Write a WARC revisit record (identical content to original).

        Body is empty — the original payload is referenced by digest.
        """
        import warcio

        now = date or datetime.now(UTC)
        record_id = f"<urn:uuid:{secrets.token_hex(16)}>"

        offset = self._buf.tell()
        self._writer.write_record(
            warcio.WARCRecord(
                type="revisit",
                uri=url,
                date=now.isoformat(),
                payload=io.BytesIO(b""),
                http_headers=None,
                warc_headers_dict={
                    "WARC-Record-ID": record_id,
                    "WARC-Refers-To": original_record_id,
                    "WARC-Profile": (
                        "http://netpreserve.org/warc/1.1/revisit/identical-payload-digest"
                    ),
                    "WARC-Block-Digest": f"sha256:{original_sha256}",
                },
            )
        )
        length = self._buf.tell() - offset

        rec = WarcRecord(
            warc_type="revisit",
            target_uri=url,
            date=now,
            content_type="application/warc-fields",
            payload=b"",
            warc_record_id=record_id,
            refers_to=original_record_id,
            sha256=original_sha256,
            offset=offset,
            length=length,
        )
        self._records.append(rec)
        return rec

    # ── rotation check ────────────────────────────────────────────────────────

    def get_bytes(self) -> bytes:
        return self._buf.getvalue()

    def needs_rotation(self) -> bool:
        return (
            self._buf.tell() >= self._max_size_bytes
            or (datetime.now(UTC) - self._created_at).total_seconds() >= self._max_age_s
        )
