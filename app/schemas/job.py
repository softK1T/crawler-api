from pydantic import BaseModel
from typing import Optional

class JobStatus(BaseModel):
    task_id: str
    state: str
    created: Optional[str] = None
    started: Optional[str] = None
    finished: Optional[str] = None

class ResultEnvelope(BaseModel):
    exists: bool
    payload: dict | None = None
