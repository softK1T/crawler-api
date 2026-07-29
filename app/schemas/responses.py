from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class TaskState(StrEnum):
    PENDING = "PENDING"
    STARTED = "STARTED"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    RETRY = "RETRY"
    REVOKED = "REVOKED"


class JobResponse(BaseModel):
    job_id: str


class BatchResponse(BaseModel):
    batch_id: str
    job_ids: list[str]
    total_count: int


class JobStatusResponse(BaseModel):
    job_id: str
    state: TaskState
    created_at: str | None = None


class BatchStatusResponse(BaseModel):
    batch_id: str
    total: int
    completed: int
    progress: float
    jobs: list[JobStatusResponse]


class CrawlResult(BaseModel):
    job_id: str
    project_id: str | None = None
    url: str
    status_code: int | None = None
    response_time_ms: int
    body: str | None = None
    body_encoding: str | None = None
    markdown: str | None = None  # HTML converted to Markdown
    extracted: dict[str, Any] | None = None  # CSS-extracted structured data
    batch_id: str | None = None
    content_type: str | None = None
    headers_trunc: dict | None = None
    error_type: str | None = None
    error_message: str | None = None
    crawled_at: str | None = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    api_key: str
    is_active: bool
    created_at: datetime
