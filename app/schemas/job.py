from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobCreate(BaseModel):
    # HttpUrl, not str: malformed URLs must be rejected at validation time
    # (422), never crash the worker while parsing a broken URL.
    url: HttpUrl
    mode: Literal["static", "stealth", "browser", "camoufox"] = "static"
    callback_url: str | None = None
    idempotency_key: str | None = Field(None, max_length=128)
    use_proxy: bool | None = None
    proxy_country: str | None = Field(None, min_length=2, max_length=2)
    proxy_city: str | None = Field(None, min_length=1, max_length=128)
    proxy_type: Literal["residential", "datacenter", "isp"] | None = None
    session_key: str | None = Field(None, min_length=1, max_length=128)
    options: dict[str, Any] = {}


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime
    idempotency_key: str | None


class JobResultResponse(BaseModel):
    job_id: str
    status: JobStatus
    result: Any | None = None
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class CallbackPayload(BaseModel):
    job_id: str
    status: JobStatus
    result: Any | None = None
    error: str | None = None
    timestamp: datetime
