"""Playwright-based fetcher — uses the shared BrowserPool for efficiency.

Fresh browser context per job (ADR-014 invariant).  The pool reuses a
single Chromium process with a semaphore to cap concurrent contexts.
"""

import asyncio
import logging
import time
from urllib.parse import urlsplit, urlunsplit

from app.services.block_detector import detect_block_reason
from app.services.fetchers.base import FetchError, FetchResult, WaitForError
from app.services.fetchers.challenge import NavigationTracker, settle_page, wait_for_selector

logger = logging.getLogger(__name__)


def _split_proxy_credentials(proxy_url: str) -> dict[str, str]:
    """Split ``http://user:pass@host:port`` into Playwright's proxy shape.

    Playwright/Chromium's ``--proxy-server`` flag does NOT accept embedded
    credentials in the server URL — it authenticates only via the separate
    ``username``/``password`` fields.  Passing the full URL as ``server``
    causes Chromium to connect anonymously, and the proxy replies 407.
    """
    parts = urlsplit(proxy_url)
    config: dict[str, str] = {}
    if parts.username:
        config["username"] = parts.username
    if parts.password:
        config["password"] = parts.password
    # Rebuild server URL without credentials.
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    server = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    config["server"] = server
    return config


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
        from app.services.fetchers.request_options import (
            current_request_options,
            without_user_agent,
        )

        request_options = current_request_options()
        pool_kwargs: dict[str, str] = {}
        if request_options and request_options.user_agent:
            pool_kwargs["ua"] = request_options.user_agent

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
                proxy_config = _split_proxy_credentials(proxy_url)
            proxy_id = getattr(proxy, "id", None)

        from app.services.fetchers.browser_guard import native_transport_enabled

        native = native_transport_enabled(proxy_config is not None)
        if native and proxy_config is not None:
            # Chromium otherwise sends loopback addresses around the proxy.
            proxy_config["bypass"] = "<-loopback>"

        if self._pool is None:
            raise FetchError(
                "BrowserPool not available — ensure worker startup completed successfully"
            )

        try:
            async with (
                asyncio.timeout(timeout_s),
                self._pool.context(proxy=proxy_config, **pool_kwargs) as ctx,
            ):
                page = await ctx.new_page()

                from app.services.fetchers.browser_guard import prepare_browser_context

                await prepare_browser_context(ctx, request_options, url, native_transport=native)
                if headers:
                    await page.set_extra_http_headers(without_user_agent(headers, request_options))

                tracker = NavigationTracker(page)
                response = await page.goto(
                    url,
                    timeout=timeout_s * 1000,
                    wait_until="domcontentloaded",
                )
                await page.wait_for_timeout(500)
                body = await settle_page(
                    page, budget_s=timeout_s - (time.perf_counter() - start) - 5.0
                )
                # A passed challenge reloads the page: describe the document now shown.
                response = tracker.response or response
                final_url = page.url
                status_code = response.status if response else 200

                from app.services.fetchers.browser_guard import check_browser_body

                check_browser_body(body)

                block_reason = detect_block_reason(status_code, {}, body)
                # No waiting on a block page or an error status: the element will not appear there.
                wait_for = request_options.wait_for if request_options else None
                if wait_for and block_reason is None and status_code < 400:
                    body = await wait_for_selector(
                        page, wait_for, budget_s=timeout_s - (time.perf_counter() - start) - 5.0
                    )
                    check_browser_body(body)
                blocked = block_reason is not None

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
                    block_reason=block_reason,
                    retries_used=0,
                    raw_body=body,  # Rendered DOM — no raw transport bytes in browser mode.
                    raw_headers={},
                )

        except WaitForError:
            raise
        except Exception as exc:
            msg = str(exc)
            if "ERR_" in msg or "net::" in msg:
                raise FetchError(f"Playwright network error: {msg}") from exc
            if "timeout" in msg.lower():
                raise FetchError(f"Request timed out: {url}") from exc
            raise FetchError(f"Playwright fetch failed: {msg}") from exc
