from pydantic import BaseModel, HttpUrl


class CrawlRequest(BaseModel):
    url: HttpUrl
    headers: dict[str, str] | None = None
    timeout: int = 30
    delay: float = 2.0
    use_proxy: bool = True
    project_id: str | None = None
    extract: dict[str, str] | None = None
    mode: str = "static"
    proxy_country: str | None = None
    wait_for: str | None = None
    session_key: str | None = None  # e.g. "shopee_sg" — injects stored cookies
