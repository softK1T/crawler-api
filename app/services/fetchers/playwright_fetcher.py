"""Playwright-based fetcher — uses the shared BrowserPool for efficiency.

Fresh browser context per job (ADR-014 invariant).  The pool reuses a
single Chromium process with a semaphore to cap concurrent contexts.
"""

import logging
import time
from urllib.parse import urlsplit, urlunsplit

from app.services.block_detector import detect_block_reason
from app.services.fetchers.base import FetchError, FetchResult

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

                block_reason = detect_block_reason(status_code, {}, body)
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

        except Exception as exc:
            msg = str(exc)
            if "ERR_" in msg or "net::" in msg:
                raise FetchError(f"Playwright network error: {msg}") from exc
            if "timeout" in msg.lower():
                raise FetchError(f"Request timed out: {url}") from exc
            raise FetchError(f"Playwright fetch failed: {msg}") from exc
