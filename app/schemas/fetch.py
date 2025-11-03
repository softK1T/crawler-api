from pydantic import BaseModel, AnyHttpUrl
from typing import Optional, Dict

class FetchRequest(BaseModel):
    url: AnyHttpUrl
    headers: Optional[Dict[str, str]] = None
    timeout_s: int | None = None

class SubmitResponse(BaseModel):
    task_id: str
