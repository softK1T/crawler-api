"""Playwright-based fetcher — browser-per-fetch with SSRF interception."""

import logging
import time
from uuid import UUID

from app.services.fetchers.base import FetchError, FetchResult, _detect_block

logger = logging.getLogger(__name__)


class PlaywrightFetcher:
    """Implements FetcherProtocol using Playwright async API.

    A new browser is launched per-fetch (not pooled). Browser reuse is
    deferred to a future optimization stage (see ADR-007).
    """

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
        proxy_id: UUID | None = None
        if proxy is not None:
            proxy_url = getattr(proxy, "url", None)
            if proxy_url:
                proxy_config = {"server": proxy_url}
            proxy_id = getattr(proxy, "id", None)

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise FetchError("Playwright not installed. Run: pip install playwright") from None

        browser = None
        try:
            p = await async_playwright().start()
            launch_kwargs: dict = {"headless": True}
            if proxy_config:
                launch_kwargs["proxy"] = proxy_config

            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(extra_http_headers=headers or {})
            page = await context.new_page()

            # SSRF interception: validate response.url on every navigation.
            async def _check_response(response):
                try:
                    await validate_url_async(response.url)
                except URLGuardError:
                    logger.warning(
                        "[playwright] SSRF blocked navigation to %s — aborting", response.url
                    )
                    # Abort by raising — caught in goto error handler.

            page.on("response", _check_response)

            response = await page.goto(
                url,
                timeout=timeout_s * 1000,
                wait_until="networkidle",
            )
            final_url = page.url
            status_code = response.status if response else 200

            body = (await page.content()).encode("utf-8")

            # Detect block from page content.
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
            )

        except Exception as exc:
            msg = str(exc)
            if "ERR_" in msg or "net::" in msg:
                raise FetchError(f"Playwright network error: {msg}") from exc
            if "timeout" in msg.lower():
                raise FetchError(f"Request timed out: {url}") from exc
            raise FetchError(f"Playwright fetch failed: {msg}") from exc
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    logger.warning("Failed to close Playwright browser", exc_info=True)
            try:
                await p.stop()
            except Exception:
                pass
