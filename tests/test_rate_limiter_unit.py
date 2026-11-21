"""Offline tests for the limits the endpoints hand to the rate limiter."""

import asyncio
import os
import unittest
import uuid
from types import SimpleNamespace
from typing import Any
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

from fastapi import HTTPException


class _DenyingRedis:
    """Records the limit of every window and rejects the call."""

    def __init__(self) -> None:
        self.limits: dict[str, int] = {}

    async def eval(self, script: str, numkeys: int, key: str, *args: int) -> list[int]:
        self.limits[key] = args[2]
        return [0, args[2], 0]


class KeyRateLimitSettingTests(unittest.TestCase):
    def test_fetch_applies_the_configured_per_key_limit(self) -> None:
        from app.api.v1.endpoints.jobs import create_fetch
        from app.core.config import settings
        from app.schemas.job import JobCreate
        from app.services.rate_limiter import RateLimiter

        redis = _DenyingRedis()
        req: Any = SimpleNamespace(
            headers={},
            app=SimpleNamespace(state=SimpleNamespace(rate_limiter=RateLimiter(redis))),
        )
        api_key: Any = SimpleNamespace(prefix="ck_test", application_id=uuid.uuid4())
        body = JobCreate.model_validate({"url": "https://www.example.com/"})

        with (
            mock.patch.object(settings, "key_rate_limit_rpm", 7),
            mock.patch(
                "app.services.policy_resolver.resolve_policy", mock.AsyncMock(return_value=None)
            ),
            self.assertRaises(HTTPException),
        ):
            asyncio.run(create_fetch(body, req, api_key, mock.Mock()))

        self.assertEqual(redis.limits["rl:key:ck_test"], 7)


def _check_all(layer: str | None) -> tuple[Any, mock.AsyncMock]:
    """Run check_all with the given layer rejecting; return the result and the quota check."""
    from app.services.rate_limiter import RateLimiter, _allowed, _denied

    def check(name: str) -> mock.AsyncMock:
        verdict = _denied if name == layer else _allowed
        return mock.AsyncMock(return_value=verdict(limit=1, count=1, reset_at_ms=0, layer=name))

    limiter = RateLimiter(None)
    with (
        mock.patch.object(limiter, "check_key", check("key")),
        mock.patch.object(limiter, "check_domain", check("domain")),
        mock.patch.object(limiter, "check_application", check("app")) as quota,
    ):
        result = asyncio.run(
            limiter.check_all(
                api_key_prefix="ck_test",
                application_id=uuid.uuid4(),
                domain="https://www.example.com/",
                proxy_id=None,
                key_rpm=60,
                domain_rps=1.0,
                monthly_quota=100,
            )
        )
    return result, quota


class QuotaChargedLastTests(unittest.TestCase):
    def test_rejected_request_does_not_use_the_quota(self) -> None:
        for layer in ("key", "domain"):
            with self.subTest(layer=layer):
                result, quota = _check_all(layer)
                self.assertEqual((result["allowed"], result["layer"]), (False, layer))
                quota.assert_not_awaited()

    def test_allowed_request_uses_the_quota_once(self) -> None:
        result, quota = _check_all(None)
        self.assertTrue(result["allowed"])
        quota.assert_awaited_once()

    def test_quota_rejection_is_returned(self) -> None:
        result, _quota = _check_all("app")
        self.assertEqual((result["allowed"], result["layer"]), (False, "app"))
