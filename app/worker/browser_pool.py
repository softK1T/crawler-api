"""Single long-lived Chromium, fresh context per job, capped concurrency."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

MAX_CONCURRENT_BROWSERS = int(os.getenv("MAX_CONCURRENT_BROWSERS", "4"))

_VIEWPORTS = [
    (1920, 1080),
    (1536, 864),
    (1440, 900),
    (1366, 768),
    (1680, 1050),
]

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['pl-PL', 'pl', 'en-US']});
Object.defineProperty(navigator, 'platform', {get: () => 'Linux x86_64'});
window.chrome = window.chrome || {runtime: {}};
"""


class ChromiumMissingError(RuntimeError):
    """Raised when the Chromium executable is not found at startup."""


async def verify_chromium() -> str:
    """Check that Chromium exists AND can actually launch.  Returns the version string."""
    async with async_playwright() as p:
        exe = Path(p.chromium.executable_path)
        if not exe.is_file():
            raise ChromiumMissingError(
                f"PLAYWRIGHT_CHROMIUM_MISSING: no executable at {exe}; "
                "rebuild the worker image (playwright install --with-deps chromium)"
            )
        browser = await p.chromium.launch()
        version = browser.version
        await browser.close()
    return version


class BrowserPool:
    """One long-lived Chromium, fresh context per job, capped concurrency.

    The browser is created lazily on first use and reconnected if the
    process dies.  Contexts are isolated — no cookies or storage leak
    between jobs (ADR-014 invariant).
    """

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_BROWSERS) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)
        self._pw: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Eagerly launch Chromium and verify it works."""
        self._pw = await async_playwright().start()
        exe = Path(self._pw.chromium.executable_path)
        if not exe.is_file():
            raise ChromiumMissingError(f"PLAYWRIGHT_CHROMIUM_MISSING: no executable at {exe}")
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        logger.info("chromium_ready version=%s path=%s", self._browser.version, str(exe))

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    @asynccontextmanager
    async def context(self, *, proxy: dict | None = None, ua: str | None = None):
        """Acquire a semaphore slot, then yield a fresh browser context.

        The browser is re-launched if the process died between calls.
        """
        async with self._sem:
            async with self._lock:
                if self._browser is None or not self._browser.is_connected():
                    await self.start()
            w, h = random.choice(_VIEWPORTS)
            ctx = await self._browser.new_context(
                proxy=proxy,
                viewport={"width": w, "height": h},
                locale="pl-PL",
                timezone_id="Europe/Warsaw",
                user_agent=ua or self._default_ua(),
                extra_http_headers={"Accept-Language": "pl-PL,pl;q=0.9,en;q=0.7"},
            )
            await ctx.add_init_script(_STEALTH_JS)
            try:
                yield ctx
            finally:
                await ctx.close()

    def _default_ua(self) -> str:
        major = (self._browser.version.split(".")[0]) if self._browser else "131"
        return (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36"
        )


# Module-level singleton — created once at import, started in worker startup.
browser_pool = BrowserPool()
