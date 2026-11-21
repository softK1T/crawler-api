"""Offline tests for a hostname that cannot be encoded for a DNS lookup.

No lookup happens here: the encoding fails before any network I/O.
"""

import asyncio
import os
import unittest
from unittest import mock

# Bound at import time: conftest replaces the guard with a no-op for every test.
from app.core.url_guard import (
    DNSResolutionError,
    URLGuardError,
    validate_url_async,
    validate_url_sync,
)

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

URLS = ("http://a..b/", f"http://{'a' * 64}.com/")


class InvalidHostnameTests(unittest.TestCase):
    def test_guard_rejects_the_hostname_by_policy(self) -> None:
        guards = {
            "async": lambda url: asyncio.run(validate_url_async(url)),
            "sync": validate_url_sync,
        }
        for url in URLS:
            for name, guard in guards.items():
                with self.subTest(url=url, guard=name):
                    with self.assertRaises(URLGuardError) as caught:
                        guard(url)
                    self.assertNotIsInstance(caught.exception, DNSResolutionError)
                    self.assertIn("Invalid hostname", str(caught.exception))

    def test_batch_with_such_a_url_answers_422(self) -> None:
        from fastapi import HTTPException

        from app.api.v1.endpoints.batches import create_crawl_batch
        from app.schemas.requests import BatchCrawlRequest

        req = mock.Mock()
        req.app.state.rate_limiter = mock.AsyncMock()
        for url in URLS:
            request = BatchCrawlRequest.model_validate({"urls": [url]})
            with self.subTest(url=url):
                with (
                    mock.patch("app.core.url_guard.validate_url_async", validate_url_async),
                    self.assertRaises(HTTPException) as caught,
                ):
                    asyncio.run(create_crawl_batch(request, req, mock.Mock(), mock.Mock()))
                self.assertEqual(caught.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
