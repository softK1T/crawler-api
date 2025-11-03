from pydantic import BaseModel
from typing import List, Optional, Any
from enum import Enum

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
    url: str
    status_code: Optional[int]
    response_time_ms: int
    body: Optional[str]
    error: Optional[str] = None
