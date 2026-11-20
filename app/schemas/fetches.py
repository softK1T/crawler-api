"""Response schemas for the fetch history endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FetchSummary(BaseModel):
    """One fetch job, summarised from its attempts inside the search window.

    engine, outcome, status_code, blocked, block_reason and error_type describe
    the last attempt, i.e. how the job ended.
    """

    job_id: str
    url: str
    domain: str
    engine: str
    outcome: str
    status_code: int | None = None
    blocked: bool
    block_reason: str | None = None
    error_type: str | None = None
    attempts: int
    duration_ms: int
    started_at: datetime
    finished_at: datetime


class FetchListResponse(BaseModel):
    items: list[FetchSummary]
    next_cursor: str | None = None
    window_from: datetime
    window_to: datetime


class FetchAttempt(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    attempt_number: int
    escalation_tier: int
    engine: str
    outcome: str
    status_code: int | None = None
    blocked: bool
    block_reason: str | None = None
    error_type: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    bytes_received: int | None = None
    proxy_type: str | None = None
    proxy_country: str | None = None
    requested_at: datetime
    completed_at: datetime | None = None


class FetchDetailResponse(BaseModel):
    job_id: str
    url: str
    domain: str
    attempts: list[FetchAttempt]
