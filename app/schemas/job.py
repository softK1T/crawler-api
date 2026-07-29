from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobCreate(BaseModel):
    url: str
    mode: Literal["static", "stealth", "browser", "camoufox"] = "static"
    callback_url: str | None = None
    idempotency_key: str | None = Field(None, max_length=128)
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
