"""curl_cffi-based fetcher — runs sync requests in a shared ThreadPoolExecutor."""

import asyncio
import atexit
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.core.url_guard import MAX_BODY_BYTES
from app.services.block_detector import detect_block_reason
from app.services.fetchers.base import FetchError, FetchResult, guard_fetch_error

logger = logging.getLogger(__name__)

# "chrome" follows the newest Chrome profile shipped with the installed curl_cffi.
# The profile sends its own User-Agent and client hints, so none is pinned here:
# a hard-coded UA drifts away from the TLS fingerprint whenever the profile moves.
IMPERSONATION = "chrome"


# ── Shared executor ───────────────────────────────────────────────────────────
_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    """Return the module-level shared executor, creating it lazily."""
    global _executor
    if _executor is None:
        max_workers = getattr(settings, "curl_executor_max_workers", 8)
        _executor = ThreadPoolExecutor(max_workers=max_workers)
        logger.info("CurlFetcher: created shared executor max_workers=%d", max_workers)
    return _executor


def _shutdown_executor() -> None:
    """Shut down the shared executor cleanly. Called from worker/app shutdown hooks."""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=True)
        _executor = None


# Ensure cleanup on process exit if the hooks didn't fire.
atexit.register(_shutdown_executor)


class CurlFetcher:
    """Implements FetcherProtocol using curl_cffi.requests.

    Sync curl_cffi calls run in a shared ``ThreadPoolExecutor`` to avoid
    blocking the event loop.  The executor is sized from config
    (``curl_executor_max_workers``, default 8) and reused across calls.
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
        from app.services.fetchers.request_options import (
            curl_identity,
            current_request_options,
            headers_for_hop,
        )

        request_options = current_request_options()
        # No default UA: the TLS profile sends the one that matches it.
        impersonation, pinned_ua = curl_identity(request_options, IMPERSONATION, None)

        # 1. Validate initial URL (sync — ok in executor context).
        from app.core.url_guard import URLGuardError, validate_url_sync

        try:
            validate_url_sync(url)
        except URLGuardError as exc:
            raise guard_fetch_error(exc) from exc

        proxy_url = None
        if proxy is not None:
            proxy_url = getattr(proxy, "url", None)
        proxy_id: UUID | None = getattr(proxy, "id", None) if proxy else None

        # 2. Run sync fetch in the shared thread executor.
        loop = asyncio.get_running_loop()

        def _fetch_hops(session: Any) -> tuple:
            """Inner synchronous fetch — runs in ThreadPoolExecutor."""
            current_url = url
            for _hop in range(max_redirects + 1):
                validate_url_sync(current_url)

                kwargs: dict = {
                    "impersonate": impersonation,
                    "allow_redirects": False,
                    "timeout": timeout_s,
                    "verify": True,
                }
                if headers or request_options:
                    merged = headers_for_hop(headers, request_options, url, current_url)
                    if pinned_ua:
                        merged["User-Agent"] = pinned_ua
                    else:
                        merged = {k: v for k, v in merged.items() if k.lower() != "user-agent"}
                    kwargs["headers"] = merged
                if proxy_url:
                    kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}

                body_buffer = bytearray()
                too_large = False

                def receive(chunk: bytes, body_buffer: bytearray = body_buffer) -> int:
                    nonlocal too_large
                    if len(body_buffer) + len(chunk) > MAX_BODY_BYTES:
                        too_large = True
                        return 0
                    body_buffer.extend(chunk)
                    return len(chunk)

                try:
                    resp = session.get(current_url, content_callback=receive, **kwargs)
                except Exception:
                    if too_large:
                        raise FetchError("Response exceeds body limit") from None
                    raise
                if too_large:
                    raise FetchError("Response exceeds body limit")

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
                    bytes(body_buffer),
                    200 <= resp.status_code < 300,
                )

            raise FetchError(f"Too many redirects from {url}")

        def _sync_fetch() -> tuple:
            """One session per fetch: cookies set on a redirect reach the next hop."""
            from curl_cffi import requests as cffi_requests

            with cffi_requests.Session() as session:
                return _fetch_hops(session)

        executor = _get_executor()
        try:
            final_url, status_code, resp_headers, body, is_success = await loop.run_in_executor(
                executor, _sync_fetch
            )
        except FetchError:
            raise
        except URLGuardError as exc:
            raise guard_fetch_error(exc) from exc
        except Exception as exc:
            raise FetchError(f"curl_cffi fetch failed: {exc}") from exc

        # 3. Detect block.
        block_reason = detect_block_reason(status_code, resp_headers, body if is_success else b"")
        blocked = block_reason is not None

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
            block_reason=block_reason,
            retries_used=0,
            raw_body=body,  # curl_cffi auto-decodes; raw ≈ decoded for now.
            raw_headers=resp_headers,
        )
