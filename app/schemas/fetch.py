"""Pydantic serialization schema for FetchResult."""

import base64
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class BlockReason(StrEnum):
    IP_BAN = "ip_ban"
    CAPTCHA = "captcha"
    CLOUDFLARE = "cloudflare"
    RATE_LIMITED = "rate_limited"
    WAF = "waf"
    OTHER = "other"
    # Vendor-specific reasons added in Phase 3.
    # Stored as plain strings in request_log — backwards compatible via _missing_.
    AKAMAI = "akamai"
    DATADOME = "datadome"
    KASADA = "kasada"
    PERIMETERX = "perimeterx"
    INCAPSULA = "incapsula"
    AWS_WAF = "aws_waf"

    @classmethod
    def _missing_(cls, value: object) -> "BlockReason":
        """Keep the public contract stable for legacy/unknown detector values."""
        if isinstance(value, str):
            legacy = {
                "bot_detection": cls.OTHER,
                "bot_detected": cls.OTHER,
                "forbidden": cls.IP_BAN,
                "access_denied": cls.IP_BAN,
                "too_many_requests": cls.RATE_LIMITED,
                "cf_challenge": cls.CLOUDFLARE,
                # vendor aliases
                "akamai_bot_manager": cls.AKAMAI,
                "dd_challenge": cls.DATADOME,
                "px_challenge": cls.PERIMETERX,
                "kp_challenge": cls.KASADA,
            }
            mapped = legacy.get(value.strip().lower())
            if mapped is not None:
                return mapped
        return cls.OTHER


def normalize_block_reason(value: Any) -> BlockReason | None:
    if value is None or value == "":
        return None
    if isinstance(value, BlockReason):
        return value
    return BlockReason(str(value).strip().lower())


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
            block_reason=normalize_block_reason(r.block_reason),
            retries_used=r.retries_used,
            trace_id=r.trace_id,
        )
