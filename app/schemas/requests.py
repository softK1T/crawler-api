from pydantic import BaseModel, HttpUrl
from typing import Optional, Dict, List, Literal


class CrawlRequest(BaseModel):
    url: HttpUrl
    project_id: Optional[str] = None
    mode: Literal["static", "stealth", "browser", "camoufox"] = "static"
    """
    Crawl mode:
      static     - httpx (fastest, no JS, blocked by most WAFs)
      stealth    - curl_cffi Chrome TLS impersonation (bypasses Cloudflare basic/medium, JA3)
      browser    - Playwright + stealth patches (handles JS, bypasses navigator.webdriver)
      camoufox   - Camoufox anti-detect Firefox (bypasses Cloudflare high, Shopee, device fingerprinting)
    """
    proxy_country: Optional[str] = None  # ISO-2 e.g. "US", "DE", "PL" — auto-detected from TLD if omitted
    extract: Optional[Dict[str, str]] = None  # CSS selectors map e.g. {"title": "h1", "price": ".price"}
    headers: Optional[Dict[str, str]] = None
    timeout: int = 30
    delay: float = 2.0
    use_proxy: bool = True
    wait_for: Optional[str] = None  # CSS selector to wait for before extracting (browser/camoufox only)


class BatchCrawlRequest(BaseModel):
    urls: List[HttpUrl]
    project_id: Optional[str] = None
    mode: Literal["static", "stealth", "browser", "camoufox"] = "static"
    proxy_country: Optional[str] = None
    extract: Optional[Dict[str, str]] = None
    headers: Optional[Dict[str, str]] = None
    timeout: int = 30
    delay: float = 2.0
    use_proxy: bool = True
    wait_for: Optional[str] = None


class ProjectCreateRequest(BaseModel):
    name: str
