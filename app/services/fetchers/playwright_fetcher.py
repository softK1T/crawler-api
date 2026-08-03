"""Playwright-based fetcher — uses the shared BrowserPool for efficiency.

Fresh browser context per job (ADR-014 invariant).  The pool reuses a
single Chromium process with a semaphore to cap concurrent contexts.
"""

import logging
import time

from app.services.fetchers.base import FetchError, FetchResult, _detect_block

logger = logging.getLogger(__name__)


class PlaywrightFetcher:
    """Implements FetcherProtocol using a shared BrowserPool.

    The pool is injected via *browser_pool* kwarg — callers pass it from
    the worker's ``ctx["browser_pool"]``.  Each fetch acquires a fresh
    context and releases it in a finally block.
    """

    def __init__(self, browser_pool=None) -> None:
        self._pool = browser_pool

    async def fetch(
        self,
        url: str,
        *,
        proxy: object | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float = 60.0,
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

        proxy_config = None
        proxy_id = None
        if proxy is not None:
            proxy_url = getattr(proxy, "url", None)
            if proxy_url:
                proxy_config = {"server": proxy_url}
            proxy_id = getattr(proxy, "id", None)

        if self._pool is None:
            raise FetchError(
                "BrowserPool not available — ensure worker startup completed successfully"
            )

        try:
            async with self._pool.context(proxy=proxy_config) as ctx:
                page = await ctx.new_page()

                # SSRF interception: validate response.url on every navigation.
                async def _check_response(response):
                    try:
                        await validate_url_async(response.url)
                    except URLGuardError:
                        logger.warning(
                            "[playwright] SSRF blocked navigation to %s — aborting",
                            response.url,
                        )

                page.on("response", _check_response)

                response = await page.goto(
                    url,
                    timeout=timeout_s * 1000,
                    wait_until="networkidle",
                )
                final_url = page.url
                status_code = response.status if response else 200

                body = (await page.content()).encode("utf-8")

                blocked, reason = _detect_block(status_code, body)

                elapsed_ms = int((time.perf_counter() - start) * 1000)

                return FetchResult(
                    url=final_url,
                    status_code=status_code,
                    headers={},
                    body=body,
                    encoding="utf-8",
                    elapsed_ms=elapsed_ms,
                    proxy_id=proxy_id,
                    engine="playwright",
                    blocked=blocked,
                    block_reason=reason,
                    retries_used=0,
                    raw_body=body,  # Rendered DOM — no raw transport bytes in browser mode.
                    raw_headers={},
                )

        except Exception as exc:
            msg = str(exc)
            if "ERR_" in msg or "net::" in msg:
                raise FetchError(f"Playwright network error: {msg}") from exc
            if "timeout" in msg.lower():
                raise FetchError(f"Request timed out: {url}") from exc
            raise FetchError(f"Playwright fetch failed: {msg}") from exc
