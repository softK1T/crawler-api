"""httpx-based fetcher — raw transport capture via stream, per-hop SSRF validation."""

import logging
import time

import httpx

from app.services.fetchers.base import FetchError, FetchResult, _detect_block

logger = logging.getLogger(__name__)


class HttpxFetcher:
    """Implements FetcherProtocol using httpx.AsyncClient.

    Uses ``client.stream()`` + ``aiter_raw()`` to capture raw transport
    bytes for WARC archival.  The body is separately decoded via the
    content-decoder module for API consumers.

    A new client is created per-fetch — connection pool sharing is
    sacrificed for proxy isolation between requests (see ADR-007).
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

        # 1. Validate initial URL.
        from app.core.url_guard import URLGuardError, validate_url_async

        try:
            await validate_url_async(url)
        except URLGuardError as exc:
            raise FetchError(str(exc), blocked=False) from exc

        # 2. Build client.
        proxy_url = None
        if proxy is not None:
            proxy_url = getattr(proxy, "url", None)
        client_kwargs: dict = {
            "follow_redirects": False,  # Manual per-hop loop.
            "timeout": httpx.Timeout(timeout_s),
        }
        if proxy_url:
            client_kwargs["proxy"] = proxy_url

        async with httpx.AsyncClient(**client_kwargs) as client:
            current_url = url

            for _hop in range(max_redirects + 1):
                # Per-hop SSRF validation.
                try:
                    await validate_url_async(current_url)
                except URLGuardError as exc:
                    raise FetchError(str(exc), blocked=False) from exc

                # Stream to capture raw transport bytes before httpx
                # applies content decoding (ADR-018).
                async with client.stream("GET", current_url, headers=headers or {}) as response:
                    raw_body = b"".join([chunk async for chunk in response.aiter_raw()])
                    raw_headers = dict(response.headers)

                    from app.services.content_decoder import decode_body, normalize_headers

                    content_encoding = raw_headers.get("content-encoding")
                    try:
                        decoded_body, _original_encoding = decode_body(raw_body, content_encoding)
                    except Exception:
                        decoded_body = raw_body

                    status_code = response.status_code

                # Manual redirect follow.
                if status_code in (301, 302, 303, 307, 308) and follow_redirects:
                    location = raw_headers.get("location")
                    if not location:
                        raise FetchError(f"Redirect ({status_code}) with no Location header")
                    from urllib.parse import urljoin

                    current_url = urljoin(current_url, location)
                    continue

                # 3. Detect block (use decoded body for content inspection).
                blocked, reason = _detect_block(
                    status_code,
                    decoded_body if status_code == 200 else b"",
                )

                elapsed_ms = int((time.perf_counter() - start) * 1000)

                return FetchResult(
                    url=current_url,
                    status_code=status_code,
                    headers=normalize_headers(raw_headers, len(decoded_body)),
                    body=decoded_body,
                    encoding="utf-8",
                    elapsed_ms=elapsed_ms,
                    proxy_id=getattr(proxy, "id", None) if proxy else None,
                    engine="httpx",
                    blocked=blocked,
                    block_reason=reason,
                    retries_used=0,
                    raw_body=raw_body,
                    raw_headers=raw_headers,
                )

            # Exhausted redirect budget.
            raise FetchError(f"Too many redirects from {url}")

    async def fetch_with_timeout(self, *args, **kwargs) -> FetchResult:
        try:
            return await self.fetch(*args, **kwargs)
        except httpx.TimeoutException as exc:
            raise FetchError(f"Request timed out: {args[0] if args else 'unknown'}") from exc
