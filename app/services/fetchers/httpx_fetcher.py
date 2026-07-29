"""httpx-based fetcher with per-hop SSRF validation."""

import logging
import time

import httpx

from app.services.fetchers.base import FetchError, FetchResult, _detect_block

logger = logging.getLogger(__name__)


class HttpxFetcher:
    """Implements FetcherProtocol using httpx.AsyncClient.

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

                response = await client.get(current_url, headers=headers or {})

                # Manual redirect follow.
                if response.status_code in (301, 302, 303, 307, 308) and follow_redirects:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError(
                            f"Redirect ({response.status_code}) with no Location header"
                        )
                    from urllib.parse import urljoin

                    current_url = urljoin(current_url, location)
                    continue

                # 3. Detect block.
                blocked, reason = _detect_block(
                    response.status_code,
                    response.content if response.status_code == 200 else b"",
                )

                elapsed_ms = int((time.perf_counter() - start) * 1000)

                return FetchResult(
                    url=current_url,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    body=response.content,
                    encoding=response.encoding or "utf-8",
                    elapsed_ms=elapsed_ms,
                    proxy_id=getattr(proxy, "id", None) if proxy else None,
                    engine="httpx",
                    blocked=blocked,
                    block_reason=reason,
                    retries_used=0,
                )

            # Exhausted redirect budget.
            raise FetchError(f"Too many redirects from {url}")

    # Prevent httpx.TimeoutException and ProxyError from leaking.
    # They are caught in fetch_with_retry as generic exceptions.

    async def fetch_with_timeout(self, *args, **kwargs) -> FetchResult:
        try:
            return await self.fetch(*args, **kwargs)
        except httpx.TimeoutException as exc:
            raise FetchError(f"Request timed out: {args[0] if args else 'unknown'}") from exc
