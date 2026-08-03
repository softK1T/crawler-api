"""Pydantic serialization schema for FetchResult."""

import base64
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class FetchResultSchema(BaseModel):
    url: str
    status_code: int
    headers: dict[str, str]
    body_b64: str
    encoding: str
    elapsed_ms: int
    proxy_id: UUID | None
    engine: str
    blocked: bool
    block_reason: str | None
    retries_used: int
    trace_id: str | None
    # ── api_version=2 integrity fields (ADR-018) ────────────────────────────
    api_version: str = "2"
    body_is_compressed: bool = False
    body_bytes: int = 0
    content_sha256: str = ""
    original_content_encoding: str | None = None

    @classmethod
    def from_result(cls, r, *, integrity: dict[str, Any] | None = None) -> "FetchResultSchema":
        ing = integrity or {}
        return cls(
            url=r.url,
            status_code=r.status_code,
            headers=r.headers,
            body_b64=base64.b64encode(r.body).decode(),
            encoding=r.encoding,
            elapsed_ms=r.elapsed_ms,
            proxy_id=r.proxy_id,
            engine=r.engine,
            blocked=r.blocked,
            block_reason=r.block_reason,
            retries_used=r.retries_used,
            trace_id=r.trace_id,
            api_version=ing.get("api_version", "2"),
            body_is_compressed=ing.get("body_is_compressed", False),
            body_bytes=ing.get("body_bytes", len(r.body)),
            content_sha256=ing.get("content_sha256", ""),
            original_content_encoding=ing.get("original_content_encoding"),
        )
