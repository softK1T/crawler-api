"""Offline tests for URL validation when a single fetch is submitted."""

import asyncio
import os
import socket
import unittest
import uuid
from types import SimpleNamespace
from typing import Any
from unittest import mock

# Bound at import time: conftest replaces the guard with a no-op for every test.
from app.core.url_guard import validate_url_async

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

from fastapi import HTTPException

_PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
_PRIVATE = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]


class FetchUrlGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.services.rate_limiter import _allowed

        verdict = _allowed(limit=60, count=1, reset_at_ms=0, layer="domain")
        self.limiter = SimpleNamespace(check_all=mock.AsyncMock(return_value=verdict))
        self.redis = mock.AsyncMock()
        self.arq_pool = mock.AsyncMock()

    def _submit(self, url: str, lookup: Any = _PUBLIC) -> int:
        """Submit one fetch with DNS answering *lookup*; return the status code."""
        from app.api.v1.endpoints.jobs import create_fetch
        from app.schemas.job import JobCreate

        state = SimpleNamespace(rate_limiter=self.limiter, redis=self.redis, arq_pool=self.arq_pool)
        req: Any = SimpleNamespace(headers={}, app=SimpleNamespace(state=state))
        api_key: Any = SimpleNamespace(
            id=uuid.uuid4(), prefix="ck_test", application_id=uuid.uuid4()
        )
        with (
            mock.patch("app.core.url_guard.validate_url_async", validate_url_async),
            mock.patch("socket.getaddrinfo", mock.Mock(side_effect=[lookup])),
            mock.patch(
                "app.services.policy_resolver.resolve_policy", mock.AsyncMock(return_value=None)
            ),
        ):
            body = JobCreate.model_validate({"url": url})
            status: int = asyncio.run(create_fetch(body, req, api_key, mock.Mock())).status_code
        return status

    def _assert_enqueued_once(self) -> None:
        self.limiter.check_all.assert_awaited_once()
        self.arq_pool.enqueue_job.assert_awaited_once()

    def test_private_address_is_rejected_before_anything_is_spent(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            self._submit("http://169.254.169.254/latest/meta-data/")
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(
            caught.exception.detail, "IP 169.254.169.254 is in a private or reserved range."
        )
        self.limiter.check_all.assert_not_awaited()
        self.assertEqual(self.redis.mock_calls, [])
        self.arq_pool.enqueue_job.assert_not_awaited()

    def test_other_policy_rejections_are_422(self) -> None:
        cases = (
            ("https://www.example.com:6379/", _PUBLIC, "Port 6379 is not allowed"),
            ("http://metadata.google.internal/", _PUBLIC, "known metadata endpoint"),
            ("https://www.example.com/", _PRIVATE, "private/reserved IP"),
            ("http://a..b/", _PUBLIC, "Invalid hostname: a..b"),
            (f"http://{'a' * 64}.com/", _PUBLIC, "Invalid hostname: aaa"),
        )
        for url, lookup, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(HTTPException) as caught:
                    self._submit(url, lookup)
                self.assertEqual(caught.exception.status_code, 422)
                self.assertIn(message, caught.exception.detail)
        self.limiter.check_all.assert_not_awaited()
        self.arq_pool.enqueue_job.assert_not_awaited()

    def test_failed_dns_lookup_is_left_to_the_worker(self) -> None:
        self.assertEqual(self._submit("https://www.example.com/", socket.gaierror()), 202)
        self._assert_enqueued_once()

    def test_guard_switched_off_accepts_the_url(self) -> None:
        from app.core.config import settings

        with mock.patch.object(settings, "ssrf_enabled", False):
            self.assertEqual(self._submit("http://169.254.169.254/latest/meta-data/"), 202)
        self._assert_enqueued_once()

    def test_public_url_is_accepted(self) -> None:
        self.assertEqual(self._submit("https://www.example.com/"), 202)
        self._assert_enqueued_once()


if __name__ == "__main__":
    unittest.main()
