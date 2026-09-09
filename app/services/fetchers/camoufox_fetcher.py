"""Camoufox-based fetcher — Firefox with humanized behaviour.

Camoufox uses Firefox (not Chromium) and cannot share the existing
BrowserPool which wraps a Playwright Chromium instance.  Per the ADR,
we use per-request browser lifecycle with a module-level asyncio.Semaphore
to cap concurrency.  See docs/decisions/ADR-020-camoufox-packaging.md.

Import is intentionally lazy so the base image (without [browser] extras)
imports cleanly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from app.services.block_detector import detect_block_reason
from app.services.fetchers.base import FetchError, FetchResult
from app.services.fetchers.playwright_fetcher import _split_proxy_credentials

logger = logging.getLogger(__name__)

MAX_CONCURRENT_CAMOUFOX = int(os.getenv("MAX_CONCURRENT_CAMOUFOX", "2"))
_sem: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Lazy semaphore — created on first use inside the event loop."""
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(MAX_CONCURRENT_CAMOUFOX)
    return _sem


@asynccontextmanager
async def _new_browser(camoufox_module: Any, **kwargs):
    """Launch a camoufox Firefox instance with the installed package API.

    Current camoufox exposes ``AsyncNewBrowser(playwright, **launch_options)``
    as an async function — it needs a started playwright instance and does
    not support ``async with`` directly.  This wrapper restores the
    context-manager shape and guarantees teardown.
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        browser = await camoufox_module.AsyncNewBrowser(pw, **kwargs)
        try:
            yield browser
        finally:
            await browser.close()
    finally:
        await pw.stop()


class CamoufoxFetcher:
    """Implements FetcherProtocol using camoufox (Firefox + humanization).

    Per-request browser lifecycle: each fetch() launches a fresh Firefox
    process, navigates, and tears it down in a finally block.

    Cost note: Firefox cold-start is ~1-2 s.  This fetcher is only reached
    at LADDER tier 5-6 after cheaper engines have failed, so the overhead
    is acceptable.  Concurrency is capped by _sem (default 2).
    """

    def __init__(
        self, browser_pool: object | None = None, camoufox_ready: bool | None = None
    ) -> None:
        # browser_pool is accepted for API compatibility with PlaywrightFetcher
        # but intentionally ignored — camoufox cannot reuse a Chromium pool.
        if browser_pool is not None:
            logger.debug(
                "CamoufoxFetcher: browser_pool ignored (Firefox uses per-request lifecycle)"
            )
        # None = self-check not run (API context, tests); False = failed at
        # worker startup — fail fast instead of a low-level launch error.
        self._camoufox_ready = camoufox_ready

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
        if self._camoufox_ready is False:
            raise FetchError("Camoufox failed self-check at worker startup — see logs")

        start = time.perf_counter()

        from app.core.url_guard import URLGuardError, validate_url_async

        try:
            await validate_url_async(url)
        except URLGuardError as exc:
            raise FetchError(str(exc), blocked=False) from exc

        # Build proxy config.
        proxy_url: str | None = None
        proxy_id = None
        proxy_country: str | None = None
        if proxy is not None:
            proxy_url = getattr(proxy, "url", None)
            proxy_id = getattr(proxy, "id", None)
            proxy_country = getattr(proxy, "country", None)

        try:
            import camoufox
        except ImportError as exc:
            raise FetchError("camoufox is not installed — install crawler-api[browser]") from exc

        sem = _get_semaphore()
        async with sem:
            return await self._do_fetch(
                camoufox=camoufox,
                url=url,
                proxy_url=proxy_url,
                proxy_id=proxy_id,
                proxy_country=proxy_country,
                headers=headers or {},
                timeout_s=timeout_s,
                start=start,
            )

    async def _do_fetch(
        self,
        *,
        camoufox: Any,
        url: str,
        proxy_url: str | None,
        proxy_id: UUID | None,
        proxy_country: str | None,
        headers: dict[str, str],
        timeout_s: float,
        start: float,
    ) -> FetchResult:
        # Launch camoufox/Firefox with humanized fingerprint patches.
        # headless="virtual" spawns Xvfb inside the container (see Dockerfile:
        # xvfb is installed) — camoufox recommends a real virtual display over
        # pure headless mode to avoid headless-detection heuristics.
        kwargs: dict[str, object] = {
            "humanize": True,
            "headless": "virtual",  # spawn Xvfb inside the container (see Dockerfile: xvfb installed)
            "geoip": proxy_country is not None,  # align TZ/locale to exit-IP country
        }
        if proxy_url:
            kwargs["proxy"] = _split_proxy_credentials(proxy_url)
        if proxy_country:
            # Pass the country so camoufox[geoip] can pick matching locale/TZ.
            kwargs["country"] = proxy_country.upper()

        try:
            async with _new_browser(camoufox, **kwargs) as browser:
                page = await browser.new_page()

                async def _check_response(response: object) -> None:
                    from app.core.url_guard import URLGuardError, validate_url_async

                    resp_url = getattr(response, "url", "")
                    if resp_url:
                        try:
                            await validate_url_async(resp_url)
                        except URLGuardError:
                            logger.warning("[camoufox] SSRF blocked navigation to %s", resp_url)

                page.on("response", _check_response)

                if headers:
                    # Let camoufox humanize provide the UA and language — a
                    # Chrome-style rotated UA over a Firefox engine is a
                    # detectable inconsistency (verified on httpbin echo).
                    # Keep Cookie (session) and other custom headers only.
                    extra = {
                        k: v
                        for k, v in headers.items()
                        if k.lower() not in ("user-agent", "accept-language")
                    }
                    if extra:
                        await page.set_extra_http_headers(extra)

                response = await page.goto(
                    url,
                    timeout=timeout_s * 1000,
                    wait_until="networkidle",
                )
                final_url: str = page.url
                status_code: int = getattr(response, "status", 200) if response else 200

                body = (await page.content()).encode("utf-8")

                # Capture response headers for WARC and block detection.
                raw_headers: dict[str, str] = {}
                if response is not None:
                    try:
                        raw_headers = dict(await response.all_headers())
                    except Exception:
                        raw_headers = {}

                block_reason = detect_block_reason(status_code, raw_headers, body)
                blocked = block_reason is not None

                elapsed_ms = int((time.perf_counter() - start) * 1000)

                return FetchResult(
                    url=final_url,
                    status_code=status_code,
                    headers=raw_headers,
                    body=body,
                    encoding="utf-8",
                    elapsed_ms=elapsed_ms,
                    proxy_id=proxy_id,
                    engine="camoufox",
                    blocked=blocked,
                    block_reason=block_reason,
                    retries_used=0,
                    raw_body=body,
                    raw_headers=raw_headers,
                )

        except FetchError:
            raise
        except Exception as exc:
            msg = str(exc)
            if "ERR_" in msg or "net::" in msg:
                raise FetchError(f"Camoufox network error: {msg}") from exc
            if "timeout" in msg.lower():
                raise FetchError(f"Camoufox request timed out: {url}") from exc
            raise FetchError(f"Camoufox fetch failed: {msg}") from exc
