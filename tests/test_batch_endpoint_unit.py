"""Offline tests for the POST /batches/ handler."""

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


def _rejected_batch(layer: str) -> HTTPException:
    """Submit a batch that the given limiter layer rejects; return the error."""
    from app.api.v1.endpoints.batches import create_crawl_batch
    from app.schemas.requests import BatchCrawlRequest
    from app.services.rate_limiter import _allowed, _denied

    def check(name: str) -> mock.AsyncMock:
        verdict = _denied if name == layer else _allowed
        return mock.AsyncMock(return_value=verdict(limit=1, count=1, reset_at_ms=0, layer=name))

    limiter = SimpleNamespace(
        check_application=check("app"), check_key=check("key"), check_domain=check("domain")
    )
    req: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(rate_limiter=limiter)))
    api_key: Any = SimpleNamespace(prefix="ck_test", application_id=uuid.uuid4())
    body = BatchCrawlRequest.model_validate({"urls": ["https://www.example.com/"]})

    with mock.patch(
        "app.services.policy_resolver.resolve_policy", mock.AsyncMock(return_value=None)
    ):
        try:
            asyncio.run(create_crawl_batch(body, req, api_key, mock.Mock()))
        except HTTPException as exc:
            return exc
    raise AssertionError("batch was not rejected")


class BatchRateLimitHitsTests(unittest.TestCase):
    def test_rejected_batch_counts_a_rate_limit_hit(self) -> None:
        for layer in ("app", "key", "domain"):
            with self.subTest(layer=layer):
                before = _hits(layer)
                self.assertEqual(_rejected_batch(layer).status_code, 429)
                self.assertEqual(_hits(layer) - before, 1.0)
