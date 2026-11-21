"""Offline tests for how the retry loop treats a URL the outbound URL guard refuses."""

import asyncio
import os
import socket
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

# Bound at import time: conftest replaces the guard with a no-op for every test.
from app.core.url_guard import validate_url_async

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

PUBLIC = "http://93.184.216.34/"
METADATA = "http://169.254.169.254/latest/meta-data/"


def _fetch(url: str, engine: str = "httpx") -> mock.Mock:
    """Run the retry loop with the real guard and fetcher; return the proxy manager."""
    from app.services.fetchers import get_fetcher
    from app.services.fetchers.base import FetchError, fetch_with_retry

    manager = mock.Mock()
    manager.get_proxy = mock.AsyncMock(side_effect=lambda **_: SimpleNamespace(id=uuid.uuid4()))
    manager.report_result = mock.AsyncMock()
    try:
        with (
            mock.patch("app.core.url_guard.validate_url_async", validate_url_async),
            mock.patch("asyncio.sleep", mock.AsyncMock()),
        ):
            asyncio.run(
                fetch_with_retry(
                    get_fetcher(engine),
                    url,
                    proxy_manager=manager,
                    use_proxy=True,
                    requested_engine=engine,
                )
            )
    except FetchError:
        return manager
    raise AssertionError("the fetch did not fail")


class RetryLoopTests(unittest.TestCase):
    def test_rejected_url_ends_the_fetch_without_retry_or_proxy_penalty(self) -> None:
        manager = _fetch("http://10.0.0.5/")
        manager.get_proxy.assert_awaited_once()
        manager.report_result.assert_not_awaited()

    def test_redirect_to_a_rejected_url_is_final_for_curl_too(self) -> None:
        session = mock.MagicMock()
        session.__enter__.return_value.get.return_value = SimpleNamespace(
            status_code=302, headers={"location": METADATA}
        )
        with mock.patch("curl_cffi.requests.Session", return_value=session):
            manager = _fetch(PUBLIC, engine="curl_cffi")
        manager.get_proxy.assert_awaited_once()
        manager.report_result.assert_not_awaited()

    def test_dns_failure_is_still_retried(self) -> None:
        for engine in ("httpx", "curl_cffi"):
            with (
                self.subTest(engine=engine),
                mock.patch("socket.getaddrinfo", side_effect=socket.gaierror("no such host")),
            ):
                manager = _fetch("https://www.example.com/", engine=engine)
                self.assertEqual(manager.get_proxy.await_count, 12)


if __name__ == "__main__":
    unittest.main()
