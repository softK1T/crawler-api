from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class CrawlRequest(BaseModel):
    url: HttpUrl
    mode: Literal["static", "stealth", "browser", "camoufox"] = "static"
    headers: dict[str, str] | None = None
    timeout: int = 30
    delay: float = 2.0
    use_proxy: bool = True
    project_id: str | None = None
    extract: dict[str, str] | None = None
    proxy_country: str | None = None
    proxy_type: Literal["residential", "datacenter"] | None = None
    wait_for: str | None = None
    session_key: str | None = None
    callback_url: HttpUrl | None = None
    idempotency_key: str | None = None
    options: dict[str, Any] = {}


class BatchCrawlRequest(BaseModel):
    urls: list[HttpUrl] = Field(..., min_length=1, max_length=100)
    mode: Literal["static", "stealth", "browser", "camoufox"] = "static"
    callback_url: HttpUrl | None = None
    options: dict[str, Any] = {}


class ProjectCreateRequest(BaseModel):
    name: str
