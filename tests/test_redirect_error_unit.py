"""Offline tests for how the retry loop treats a redirect that cannot be followed."""

import asyncio
import os
import unittest
import uuid
from types import SimpleNamespace
from typing import Any
from unittest import mock

import httpx

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

URL = "https://loop.example/x"


def _fetch(engine: str, headers: dict[str, str]) -> tuple[mock.Mock, list[Any]]:
    """Run the retry loop with the real fetcher against a site that always answers 302."""
    from app.services.fetchers import get_fetcher
    from app.services.fetchers.base import FetchError, fetch_with_retry

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers=headers, stream=httpx.ByteStream(b""))

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    session = mock.MagicMock()
    session.__enter__.return_value.get.return_value = SimpleNamespace(
        status_code=302, headers=headers
    )
    manager = mock.Mock()
    manager.get_proxy = mock.AsyncMock(side_effect=lambda **_: SimpleNamespace(id=uuid.uuid4()))
    manager.report_result = mock.AsyncMock()
    attempts: list[Any] = []

    async def record(attempt: Any) -> None:
        attempts.append(attempt)

    try:
        with (
            mock.patch("httpx.AsyncClient", lambda **kw: real_client(transport=transport, **kw)),
            mock.patch("curl_cffi.requests.Session", return_value=session),
            mock.patch("app.core.url_guard.validate_url_sync"),
            mock.patch("asyncio.sleep", mock.AsyncMock()),
        ):
            asyncio.run(
                fetch_with_retry(
                    get_fetcher(engine),
                    URL,
                    proxy_manager=manager,
                    use_proxy=True,
                    requested_engine=engine,
                    attempt_recorder=record,
                )
            )
    except FetchError:
        return manager, attempts
    raise AssertionError("the fetch did not fail")


class RetryLoopTests(unittest.TestCase):
    def test_unfollowable_redirect_ends_the_fetch_without_retry_or_proxy_penalty(self) -> None:
        cases: tuple[tuple[dict[str, str], str], ...] = (
            ({"location": URL}, f"Too many redirects from {URL}"),
            ({}, "Redirect (302) with no Location header"),
        )
        for engine in ("httpx", "curl_cffi"):
            for headers, message in cases:
                with self.subTest(engine=engine, message=message):
                    manager, attempts = _fetch(engine, headers)
                    manager.get_proxy.assert_awaited_once()
                    manager.report_result.assert_not_awaited()
                    self.assertEqual(
                        [(a.outcome, a.error_type, a.error) for a in attempts],
                        [("fetch_error", "RedirectError", message)],
                    )


if __name__ == "__main__":
    unittest.main()
