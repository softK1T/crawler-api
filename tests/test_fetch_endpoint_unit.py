"""Offline tests for the POST /v1/fetch handler."""

import asyncio
import os
import unittest
import uuid
from types import SimpleNamespace
from typing import Any
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

from fastapi import HTTPException
from prometheus_client import REGISTRY


def _hits(layer: str) -> float:
    return REGISTRY.get_sample_value("crawler_rate_limit_hits_total", {"layer": layer}) or 0.0


class RateLimitHitsMetricTests(unittest.TestCase):
    def test_rejected_fetch_counts_a_rate_limit_hit(self) -> None:
        from app.api.v1.endpoints.jobs import create_fetch
        from app.schemas.job import JobCreate
        from app.services.rate_limiter import _denied

        denied = _denied(limit=60, count=60, reset_at_ms=0, layer="key")
        limiter = SimpleNamespace(check_all=mock.AsyncMock(return_value=denied))
        req: Any = SimpleNamespace(
            headers={}, app=SimpleNamespace(state=SimpleNamespace(rate_limiter=limiter))
        )
        api_key: Any = SimpleNamespace(prefix="ck_test", application_id=uuid.uuid4())
        body = JobCreate.model_validate({"url": "https://www.example.com/"})
        before = _hits("key")

        with (
            mock.patch(
                "app.services.policy_resolver.resolve_policy", mock.AsyncMock(return_value=None)
            ),
            self.assertRaises(HTTPException) as raised,
        ):
            asyncio.run(create_fetch(body, req, api_key, mock.Mock()))

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(_hits("key") - before, 1.0)
