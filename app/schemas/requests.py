from pydantic import BaseModel, HttpUrl
from typing import Optional, Dict, List


class CrawlRequest(BaseModel):
    url: HttpUrl
    headers: Optional[Dict[str, str]] = None
    timeout: int = 15
    delay: float = 1.0
    use_proxy: bool = True


class BatchCrawlRequest(BaseModel):
    urls: List[HttpUrl]
    headers: Optional[Dict[str, str]] = None
    timeout: int = 15
    delay: float = 1.0
    use_proxy: bool = True
