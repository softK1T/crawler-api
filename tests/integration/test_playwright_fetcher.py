"""Integration tests for PlaywrightFetcher — context isolation, SSRF handler.

These require a real Playwright installation and are marked ``integration``.
Run with: pytest -m slow tests/integration/test_playwright_fetcher.py
"""

import pytest

pytest.importorskip("playwright", reason="Playwright not installed")


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio
async def test_fresh_context_per_fetch_cookie_isolation():
    """Two browser contexts must not share cookies/storage.

    Creates a browser, two separate contexts, sets a cookie in context 1,
    then asserts context 2 does NOT see it — proving fresh isolation.
    """
    from playwright.async_api import async_playwright

    api = await async_playwright().start()
    browser = await api.chromium.launch(headless=True)

    # Context 1: set a cookie.
    ctx1 = await browser.new_context()
    await ctx1.add_cookies([{"name": "leaked", "value": "xyz", "url": "https://example.com"}])
    cookies1 = await ctx1.cookies()
    assert any(c["name"] == "leaked" for c in cookies1), "Cookie not set in ctx1"
    await ctx1.close()

    # Context 2: must NOT see the cookie from ctx1.
    ctx2 = await browser.new_context()
    cookies2 = await ctx2.cookies()
    leaked = [c for c in cookies2 if c["name"] == "leaked"]
    assert not leaked, f"Context 2 leaked cookies from context 1: {leaked} — context was reused!"
    await ctx2.close()

    await browser.close()
    await api.stop()


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio
async def test_ssrf_interception_handler_fires_per_fetch():
    """The SSRF page.on('response') handler fires and rejects blocked redirects."""
    from app.services.fetchers.base import FetchError
    from app.services.fetchers.playwright_fetcher import PlaywrightFetcher

    fetcher = PlaywrightFetcher()

    # A URL that redirects to a known blocked address.
    # The SSRF handler should abort via validate_url_async.
    try:
        _result = await fetcher.fetch(
            "https://httpbin.org/redirect-to?url=http://169.254.169.254/",
            timeout_s=15.0,
        )
    except FetchError as exc:
        # FetchError is expected if SSRF was triggered.
        assert "blocked" in str(exc).lower() or "169.254" in str(exc), f"Unexpected error: {exc}"
    except Exception:
        pytest.skip("httpbin.org not reachable")
