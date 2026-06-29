from pydantic import BaseModel
from typing import List, Optional, Any, Dict
from enum import Enum
from datetime import datetime


class TaskState(str, Enum):
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
    job_ids: List[str]
    total_count: int


class JobStatusResponse(BaseModel):
    job_id: str
    state: TaskState
    created_at: Optional[str] = None


class BatchStatusResponse(BaseModel):
    batch_id: str
    total: int
    completed: int
    progress: float
    jobs: List[JobStatusResponse]


class CrawlResult(BaseModel):
    job_id: str
    project_id: Optional[str] = None
    url: str
    status_code: Optional[int] = None
    response_time_ms: int
    body: Optional[str] = None
    body_encoding: Optional[str] = None
    markdown: Optional[str] = None          # HTML converted to Markdown
    extracted: Optional[Dict[str, Any]] = None  # CSS-extracted structured data
    batch_id: Optional[str] = None
    content_type: Optional[str] = None
    headers_trunc: Optional[dict] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    crawled_at: Optional[str] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    api_key: str
    is_active: bool
    created_at: datetime
