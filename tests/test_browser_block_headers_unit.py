"""Offline tests: Playwright passes the response headers to block detection."""

import asyncio
import os
import unittest
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

URL = "https://www.example.pl/"
CLOUDFLARE_HEADERS = {"cf-ray": "8f1c2d3e4a5b6c7d-WAW", "server": "cloudflare"}


class _FakeResponse:
    def __init__(self, status: int, headers: dict[str, str] | Exception) -> None:
        self.status = status
        self._headers = headers

    async def all_headers(self) -> dict[str, str]:
        if isinstance(self._headers, Exception):
            raise self._headers
        return self._headers


class _FakePage:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.url = URL
        self.main_frame = object()

    def on(self, event: str, handler: Any) -> None:
        return None

    async def set_extra_http_headers(self, headers: dict[str, str]) -> None:
        return None

    async def goto(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._response

    async def wait_for_timeout(self, delay_ms: float) -> None:
        return None

    async def content(self) -> str:
        return "<html><body>Access denied</body></html>"


class _FakePool:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    @asynccontextmanager
    async def context(self, **kwargs: Any) -> Any:
        yield SimpleNamespace(new_page=mock.AsyncMock(return_value=_FakePage(self._response)))


def _fetch(response: _FakeResponse, **kwargs: Any) -> Any:
    from app.services.fetchers.playwright_fetcher import PlaywrightFetcher

    return PlaywrightFetcher(browser_pool=_FakePool(response)).fetch(URL, **kwargs)


def _offline() -> Any:
    return (
        mock.patch("app.core.url_guard.validate_url_async", mock.AsyncMock()),
        mock.patch("app.services.fetchers.browser_guard.prepare_browser_context", mock.AsyncMock()),
    )


class PlaywrightHeaderTests(unittest.TestCase):
    def test_403_with_cf_ray_is_a_cloudflare_block_and_keeps_the_headers(self) -> None:
        from app.schemas.fetch import BlockReason

        guard, context = _offline()
        with guard, context:
            result = asyncio.run(_fetch(_FakeResponse(403, CLOUDFLARE_HEADERS)))
        self.assertTrue(result.blocked)
        self.assertEqual(result.block_reason, BlockReason.CLOUDFLARE)
        self.assertEqual(result.headers, CLOUDFLARE_HEADERS)
        self.assertEqual(result.raw_headers, CLOUDFLARE_HEADERS)

    def test_unreadable_headers_still_give_a_result(self) -> None:
        from app.schemas.fetch import BlockReason

        guard, context = _offline()
        with guard, context:
            result = asyncio.run(_fetch(_FakeResponse(403, RuntimeError("response is gone"))))
        self.assertEqual(result.status_code, 403)
        self.assertEqual(result.block_reason, BlockReason.IP_BAN)
        self.assertEqual(result.headers, {})
        self.assertEqual(result.raw_headers, {})

    def test_cloudflare_403_in_playwright_moves_the_ladder_to_camoufox(self) -> None:
        from app.core.config import settings
        from app.services.fetchers.base import FetchResult, fetch_with_retry

        engines: list[str] = []

        def get_fetcher(engine: str, **kwargs: Any) -> Any:
            async def fetch(url: str, **kw: Any) -> Any:
                engines.append(engine)
                if engine == "playwright":
                    return await _fetch(_FakeResponse(403, CLOUDFLARE_HEADERS), **kw)
                return FetchResult(url=url, status_code=200, engine=engine)

            return SimpleNamespace(_engine_name=engine, fetch=fetch)

        async def get_proxy(**kwargs: Any) -> Any:
            return SimpleNamespace(id=uuid.uuid4(), country="PL")

        proxies = SimpleNamespace(get_proxy=get_proxy, report_result=mock.AsyncMock())
        guard, context = _offline()
        with (
            guard,
            context,
            mock.patch("app.services.fetchers.get_fetcher", get_fetcher),
            mock.patch("asyncio.sleep", mock.AsyncMock()),
            mock.patch.object(settings, "enable_premium_proxy_tiers", False),
            mock.patch.dict(os.environ, {"PREMIUM_PROXY_DOMAINS": ""}),
        ):
            result = asyncio.run(
                fetch_with_retry(
                    get_fetcher("playwright"),
                    URL,
                    proxy_manager=proxies,  # type: ignore[arg-type]
                    requested_engine="playwright",
                )
            )
        self.assertEqual(engines, ["playwright", "playwright", "camoufox"])
        self.assertEqual(result.engine, "camoufox")
        self.assertFalse(result.blocked)


if __name__ == "__main__":
    unittest.main()
