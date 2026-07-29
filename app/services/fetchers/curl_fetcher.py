"""curl_cffi-based fetcher — runs sync requests in ThreadPoolExecutor."""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from app.services.fetchers.base import FetchError, FetchResult, _detect_block

logger = logging.getLogger(__name__)

IMPERSONATION = "chrome120"


class CurlFetcher:
    """Implements FetcherProtocol using curl_cffi.requests.

    The sync curl_cffi call runs in a ``ThreadPoolExecutor`` to avoid
    blocking the event loop and to sidestep the gevent+asyncio conflict.
    """

    async def fetch(
        self,
        url: str,
        *,
        proxy: object | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float = 30.0,
        follow_redirects: bool = True,
        max_redirects: int = 10,
    ) -> FetchResult:
        start = time.perf_counter()

        # 1. Validate initial URL (sync — ok in executor context).
        from app.core.url_guard import URLGuardError, validate_url_sync

        try:
            validate_url_sync(url)
        except URLGuardError as exc:
            raise FetchError(str(exc), blocked=False) from exc

        proxy_url = None
        if proxy is not None:
            proxy_url = getattr(proxy, "url", None)
        proxy_id: UUID | None = getattr(proxy, "id", None) if proxy else None

        # 2. Run sync fetch in thread executor.
        loop = asyncio.get_running_loop()

        def _sync_fetch() -> tuple:
            """Inner synchronous fetch — runs in ThreadPoolExecutor."""
            from curl_cffi import requests as cffi_requests

            current_url = url
            for _hop in range(max_redirects + 1):
                validate_url_sync(current_url)

                kwargs: dict = {
                    "impersonate": IMPERSONATION,
                    "allow_redirects": False,
                    "timeout": timeout_s,
                    "verify": True,
                }
                if headers:
                    kwargs["headers"] = headers
                if proxy_url:
                    kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}

                resp = cffi_requests.get(current_url, **kwargs)

                if resp.status_code in (301, 302, 303, 307, 308) and follow_redirects:
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError(f"Redirect ({resp.status_code}) with no Location header")
                    from urllib.parse import urljoin

                    current_url = urljoin(current_url, location)
                    continue

                return (
                    current_url,
                    resp.status_code,
                    dict(list(resp.headers.items())[:50]),
                    resp.content,
                    200 <= resp.status_code < 300,
                )

            raise FetchError(f"Too many redirects from {url}")

        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                final_url, status_code, resp_headers, body, is_success = await loop.run_in_executor(
                    executor, _sync_fetch
                )
            except FetchError:
                raise
            except Exception as exc:
                raise FetchError(f"curl_cffi fetch failed: {exc}") from exc

        # 3. Detect block.
        blocked, reason = _detect_block(status_code, body if is_success else b"")

        elapsed_ms = int((time.perf_counter() - start) * 1000)

        return FetchResult(
            url=final_url,
            status_code=status_code,
            headers=resp_headers,
            body=body,
            encoding="utf-8",
            elapsed_ms=elapsed_ms,
            proxy_id=proxy_id,
            engine="curl_cffi",
            blocked=blocked,
            block_reason=reason,
            retries_used=0,
        )
