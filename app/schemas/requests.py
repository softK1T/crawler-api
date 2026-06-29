from typing import Optional, Dict
from pydantic import BaseModel, HttpUrl


class CrawlRequest(BaseModel):
    url: HttpUrl
    headers: Optional[Dict[str, str]] = None
    timeout: int = 30
    delay: float = 2.0
    use_proxy: bool = True
    project_id: Optional[str] = None
    extract: Optional[Dict[str, str]] = None
    mode: str = "static"
    proxy_country: Optional[str] = None
    wait_for: Optional[str] = None
    session_key: Optional[str] = None  # e.g. "shopee_sg" — injects stored cookies
