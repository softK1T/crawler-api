"""Camoufox runtime self-check — mirror of browser_pool.verify_chromium().

Unlike Chromium, camoufox is only needed at ladder tiers 5-6, so a failed
self-check is NON-fatal for the worker: startup logs the error and sets
ctx["camoufox_ready"] = False; CamoufoxFetcher then fails fast with a clear
message instead of a low-level Firefox launch error.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 20.0


class CamoufoxMissingError(RuntimeError):
    """Raised when camoufox cannot be imported or Firefox fails to launch."""


async def verify_camoufox() -> str:
    """Launch a real camoufox/Firefox instance and return its version string.

    Import stays local so the API image (no [browser] extras) can import this
    module without ImportError.  Any launch failure raises
    CamoufoxMissingError with a rebuild hint.
    """
    try:
        import camoufox
    except ImportError as exc:
        raise CamoufoxMissingError(
            "CAMOUFOX_FIREFOX_MISSING: camoufox package not installed; "
            "rebuild worker image with camoufox fetch"
        ) from exc

    async def _launch_tick() -> str:
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        try:
            browser = await camoufox.AsyncNewBrowser(pw, headless=True)
            try:
                page = await browser.new_page()
                await page.goto("about:blank")
                await page.close()
                return str(browser.version)
            finally:
                await browser.close()
        finally:
            await pw.stop()

    try:
        return await asyncio.wait_for(_launch_tick(), timeout=CHECK_TIMEOUT_S)
    except TimeoutError as exc:
        raise CamoufoxMissingError(
            f"CAMOUFOX_FIREFOX_MISSING: launch timed out after {CHECK_TIMEOUT_S}s; "
            "rebuild worker image with camoufox fetch"
        ) from exc
    except Exception as exc:
        raise CamoufoxMissingError(
            f"CAMOUFOX_FIREFOX_MISSING: {type(exc).__name__}: {exc}; "
            "rebuild worker image with camoufox fetch"
        ) from exc
