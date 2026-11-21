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
