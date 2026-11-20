"""Offline tests for browser failure handling: route errors and the Camoufox GeoIP file."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")


class _FakeContext:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    async def route(self, pattern: str, handler: Any) -> None:
        self.handlers.append(handler)

    async def route_web_socket(self, pattern: str, handler: Any) -> None:
        return None


def _run_route(route: Any) -> None:
    from app.services.fetchers import browser_guard

    async def scenario() -> None:
        context = _FakeContext()
        with mock.patch.object(browser_guard, "validate_url_async", mock.AsyncMock()):
            await browser_guard.install_browser_guard(context)
            await context.handlers[0](route)

    asyncio.run(scenario())


class RouteFailureTests(unittest.TestCase):
    def test_fetch_error_aborts_the_route_and_does_not_escape(self) -> None:
        route = mock.Mock()
        route.request.url = "https://www.example.pl/"
        route.fetch = mock.AsyncMock(side_effect=RuntimeError("Request context disposed"))
        route.abort = mock.AsyncMock()
        _run_route(route)
        route.abort.assert_awaited_once_with("failed")

    def test_abort_and_dispose_errors_are_swallowed(self) -> None:
        response = mock.Mock()
        response.headers = {}
        response.body = mock.AsyncMock(side_effect=RuntimeError("read ECONNRESET"))
        response.dispose = mock.AsyncMock(side_effect=RuntimeError("context closed"))
        route = mock.Mock()
        route.request.url = "https://www.example.pl/"
        route.fetch = mock.AsyncMock(return_value=response)
        route.abort = mock.AsyncMock(side_effect=RuntimeError("context closed"))
        _run_route(route)
        route.abort.assert_awaited_once_with("failed")
        response.dispose.assert_awaited_once()


class CamoufoxGeoipTests(unittest.TestCase):
    def test_geoip_requires_the_bundled_database(self) -> None:
        from app.services.fetchers.camoufox_fetcher import _geoip_database_ready

        with tempfile.TemporaryDirectory() as folder:
            module = SimpleNamespace(__file__=os.path.join(folder, "__init__.py"))
            self.assertFalse(_geoip_database_ready(module))
            Path(folder, "GeoLite2-City.mmdb").write_bytes(b"x")
            self.assertTrue(_geoip_database_ready(module))
        self.assertFalse(_geoip_database_ready(SimpleNamespace()))


if __name__ == "__main__":
    unittest.main()
