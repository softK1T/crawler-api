from pydantic import BaseModel, AnyHttpUrl, Field
from typing import Optional, Dict, List

class CrawlRequest(BaseModel):
    url: AnyHttpUrl
    headers: Optional[Dict[str, str]] = None
    timeout: int = Field(default=15, ge=1, le=300, description="Timeout in seconds")

class BatchCrawlRequest(BaseModel):
    urls: List[AnyHttpUrl] = Field(..., min_items=1, max_items=100)
    headers: Optional[Dict[str, str]] = None
    timeout: int = Field(default=15, ge=1, le=300, description="Timeout in seconds")
