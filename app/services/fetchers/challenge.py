"""Give an interstitial JavaScript challenge time to finish before the page is read."""

from __future__ import annotations

import os
import random
import re
import time
from typing import Any

from app.services.fetchers.base import WaitForError

CHALLENGE_WAIT_S = float(os.getenv("BROWSER_CHALLENGE_WAIT_S", "20"))
_POLL_MS = 1000

# Markers of a challenge that resolves by itself once its script has run. A hard
# block page carries none of them, so it is returned at once instead of waited on.
_PENDING_CHALLENGE = re.compile(rb"_cf_chl_opt|\bcf-chl-|challenge-error-text", re.I)


def challenge_pending(body: bytes) -> bool:
    """True while *body* is a challenge page that may still clear on its own."""
    return _PENDING_CHALLENGE.search(body[:1_000_000]) is not None


class NavigationTracker:
    """Remembers the latest top-level document response of a page.

    A passed challenge reloads the page, so the response returned by ``goto``
    (usually a 403) no longer describes what the page shows.
    """

    def __init__(self, page: Any) -> None:
        self._page = page
        self.response: Any = None
        page.on("response", self._on_response)

    def _on_response(self, response: Any) -> None:
        try:
            request = response.request
            if request.is_navigation_request() and request.frame is self._page.main_frame:
                self.response = response
        except Exception:
            return


async def _idle_like_a_visitor(page: Any) -> None:
    """Move the pointer and scroll a little while a challenge runs; never fails the fetch."""
    try:
        x = random.uniform(200, 900)  # noqa: S311
        y = random.uniform(150, 600)  # noqa: S311
        steps = random.randint(8, 20)  # noqa: S311
        await page.mouse.move(x, y, steps=steps)
        await page.mouse.wheel(0, random.uniform(40, 220))  # noqa: S311
    except Exception:
        return


async def settle_page(page: Any, *, budget_s: float) -> bytes:
    """Return the serialized DOM, waiting up to *budget_s* for a pending challenge."""
    limit = time.monotonic() + max(0.0, min(budget_s, CHALLENGE_WAIT_S))
    body = b""
    while True:
        try:
            body = (await page.content()).encode("utf-8")
        except Exception:
            # The challenge is navigating to the real page; read again once it lands.
            body = b""
            pending = True
        else:
            pending = challenge_pending(body)
        if not pending or time.monotonic() >= limit:
            break
        await _idle_like_a_visitor(page)
        await page.wait_for_timeout(_POLL_MS)
    if not body:
        body = (await page.content()).encode("utf-8")
    return body


async def wait_for_selector(page: Any, selector: str, *, budget_s: float) -> bytes:
    """Return the serialized DOM once *selector* is in it, visible or not."""
    try:
        # Playwright reads a timeout of 0 as no limit, so a spent budget still gets a second.
        timeout_ms = int(max(budget_s, 1.0) * 1000)
        await page.wait_for_selector(selector, state="attached", timeout=timeout_ms)
    except Exception as exc:
        reason = str(exc).partition("\n")[0]
        raise WaitForError(f"wait_for failed: {reason}") from exc
    body: bytes = (await page.content()).encode("utf-8")
    return body
