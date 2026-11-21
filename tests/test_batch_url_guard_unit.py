"""Offline tests for URL validation when a batch is submitted."""

import asyncio
import os
import socket
import threading
import unittest
from unittest import mock

# Bound at import time: conftest replaces the guard with a no-op for every test.
from app.core.url_guard import validate_url_async

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")


def _submit(url: str) -> None:
    from app.api.v1.endpoints.batches import create_crawl_batch
    from app.schemas.requests import BatchCrawlRequest

    req = mock.Mock()
    req.app.state.rate_limiter = mock.AsyncMock()
    with (
        mock.patch("app.core.url_guard.validate_url_async", validate_url_async),
        mock.patch(
            "app.services.policy_resolver.resolve_policy", mock.AsyncMock(return_value=None)
        ),
        mock.patch("app.api.v1.endpoints.batches.BatchService.create_batch", mock.AsyncMock()),
    ):
        request = BatchCrawlRequest.model_validate({"urls": [url]})
        asyncio.run(create_crawl_batch(request, req, mock.Mock(), mock.Mock()))


class BatchUrlGuardTests(unittest.TestCase):
    def test_refused_url_is_rejected_with_422(self) -> None:
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as caught:
            _submit("http://127.0.0.1/")
        self.assertEqual(caught.exception.status_code, 422)

    def test_dns_lookup_runs_off_the_event_loop(self) -> None:
        lookup_threads: list[int] = []

        def lookup(*_args: object, **_kwargs: object) -> list[tuple]:
            lookup_threads.append(threading.get_ident())
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

        with mock.patch("socket.getaddrinfo", lookup):
            _submit("https://www.example.com/")
        self.assertEqual(len(lookup_threads), 1)
        self.assertNotIn(threading.get_ident(), lookup_threads)


if __name__ == "__main__":
    unittest.main()
