"""Pydantic serialization schema for FetchResult."""

import base64
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class BlockReason(StrEnum):
    IP_BAN = "ip_ban"
    CAPTCHA = "captcha"
    CLOUDFLARE = "cloudflare"
    RATE_LIMITED = "rate_limited"
    WAF = "waf"
    OTHER = "other"


class FetchResultSchema(BaseModel):
    api_version: int = 2
    url: str
    status_code: int
    headers: dict[str, str]
    body_b64: str
    body_is_compressed: bool = False
    body_bytes: int
    content_sha256: str
    original_content_encoding: str | None = None
    encoding: str = "utf-8"
    elapsed_ms: int
    proxy_id: UUID | None = None
    proxy_country: str | None = None
    engine: str
    blocked: bool = False
    block_reason: BlockReason | None = None
    retries_used: int = 0
    trace_id: str | None = None

    @classmethod
    def from_result(cls, r, *, integrity_fields: dict | None = None) -> "FetchResultSchema":
        ing = integrity_fields or {}
        return cls(
            api_version=2,
            url=r.url,
            status_code=r.status_code,
            headers=r.headers,
            body_b64=base64.b64encode(r.body).decode(),
            body_is_compressed=False,
            body_bytes=ing.get("body_bytes", len(r.body)),
            content_sha256=ing.get("content_sha256", ""),
            original_content_encoding=ing.get("original_content_encoding"),
            encoding=r.encoding,
            elapsed_ms=r.elapsed_ms,
            proxy_id=r.proxy_id,
            proxy_country=getattr(r, "proxy_country", None),
            engine=r.engine,
            blocked=r.blocked,
            block_reason=BlockReason(r.block_reason) if r.block_reason else None,
            retries_used=r.retries_used,
            trace_id=r.trace_id,
        )
