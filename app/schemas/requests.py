from pydantic import BaseModel, HttpUrl
from typing import Optional, Dict, List, Literal


class CrawlRequest(BaseModel):
    url: HttpUrl
    project_id: Optional[str] = None
    mode: Literal["static", "browser"] = "static"
    proxy_country: Optional[str] = None  # ISO-2 e.g. "US", "DE", "PL" — auto-detected from TLD if omitted
    extract: Optional[Dict[str, str]] = None  # CSS selectors map e.g. {"title": "h1", "price": ".price"}
    headers: Optional[Dict[str, str]] = None
    timeout: int = 15
    delay: float = 1.0
    use_proxy: bool = True


class BatchCrawlRequest(BaseModel):
    urls: List[HttpUrl]
    project_id: Optional[str] = None
    mode: Literal["static", "browser"] = "static"
    proxy_country: Optional[str] = None
    extract: Optional[Dict[str, str]] = None
    headers: Optional[Dict[str, str]] = None
    timeout: int = 15
    delay: float = 1.0
    use_proxy: bool = True


class ProjectCreateRequest(BaseModel):
    name: str
