"""Pydantic serialization schema for FetchResult."""

import base64
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

    @classmethod
    def from_result(cls, r) -> "FetchResultSchema":
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
        )
