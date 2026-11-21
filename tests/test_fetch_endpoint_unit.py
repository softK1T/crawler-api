"""Offline tests for the POST /v1/fetch handler."""

import asyncio
import json
import os
import unittest
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

from fastapi import HTTPException
from prometheus_client import REGISTRY


def _hits(layer: str) -> float:
    return REGISTRY.get_sample_value("crawler_rate_limit_hits_total", {"layer": layer}) or 0.0


def _observed(status_code: str) -> float:
    labels = {
        "component": "api",
        "endpoint": "/v1/fetch",
        "method": "POST",
        "status_code": status_code,
    }
    return REGISTRY.get_sample_value("crawler_request_latency_ms_count", labels) or 0.0


class FetchLatencyMetricTests(unittest.TestCase):
    def _fetch(self, payload: dict[str, Any], *, allowed: bool = True, replay: bool = False) -> int:
        from app.api.v1.endpoints.jobs import create_fetch
        from app.schemas.job import JobCreate, JobResultResponse, JobStatus
        from app.services.rate_limiter import _allowed, _denied

        verdict = (_allowed if allowed else _denied)(limit=60, count=1, reset_at_ms=0, layer="key")
        limiter = SimpleNamespace(check_all=mock.AsyncMock(return_value=verdict))
        req: Any = SimpleNamespace(
            headers={}, app=SimpleNamespace(state=mock.Mock(rate_limiter=limiter))
        )
        api_key: Any = SimpleNamespace(prefix="ck_test", application_id=uuid.uuid4())
        job_svc = mock.AsyncMock()
        job_svc.handle_idempotency.return_value = "job-1" if replay else None
        job_svc.get_status.return_value = JobStatus.COMPLETED
        job_svc.get_result.return_value = JobResultResponse(
            job_id="job-1", status=JobStatus.COMPLETED, created_at=datetime.now(UTC)
        )
        body = JobCreate.model_validate({"url": "https://www.example.com/", **payload})
        with (
            mock.patch(
                "app.services.policy_resolver.resolve_policy", mock.AsyncMock(return_value=None)
            ),
            mock.patch("app.services.job_service.JobService", return_value=job_svc),
        ):
            try:
                status: int = asyncio.run(create_fetch(body, req, api_key, mock.Mock())).status_code
            except HTTPException as exc:
                status = exc.status_code
        return status

    def test_every_exit_is_observed_once_under_its_status_code(self) -> None:
        exits: list[tuple[str, int, dict[str, Any], dict[str, bool]]] = [
            ("completed sync fetch", 200, {"options": {"sync": True}}, {}),
            ("idempotent replay", 200, {"idempotency_key": "k"}, {"replay": True}),
            ("accepted async fetch", 202, {}, {}),
            ("rate limited", 429, {}, {"allowed": False}),
        ]
        for name, status_code, payload, limits in exits:
            with self.subTest(name):
                before = _observed(str(status_code))
                self.assertEqual(self._fetch(payload, **limits), status_code)
                self.assertEqual(_observed(str(status_code)) - before, 1.0)


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


class SharedRedisClientTests(unittest.TestCase):
    def test_fetch_enqueues_through_the_app_client(self) -> None:
        from app.api.v1.endpoints.jobs import create_fetch
        from app.schemas.job import JobCreate
        from app.services.rate_limiter import _allowed

        allowed = _allowed(limit=1, count=1, reset_at_ms=0, layer="domain")
        limiter = SimpleNamespace(check_all=mock.AsyncMock(return_value=allowed))
        shared = mock.AsyncMock()
        shared.llen.return_value = 0
        req: Any = SimpleNamespace(
            headers={},
            app=SimpleNamespace(state=SimpleNamespace(rate_limiter=limiter, redis=shared)),
        )
        api_key: Any = SimpleNamespace(
            id=uuid.uuid4(), prefix="ck_test", application_id=uuid.uuid4()
        )
        body = JobCreate.model_validate({"url": "https://www.example.com/"})

        with (
            mock.patch(
                "app.services.policy_resolver.resolve_policy", mock.AsyncMock(return_value=None)
            ),
            mock.patch("arq.create_pool", mock.AsyncMock()),
            mock.patch("redis.asyncio.from_url", return_value=mock.AsyncMock()) as from_url,
        ):
            response = asyncio.run(create_fetch(body, req, api_key, mock.Mock()))

        self.assertEqual(response.status_code, 202)
        from_url.assert_not_called()
        job_id = json.loads(response.body)["job_id"]
        self.assertEqual(shared.set.await_args.args[0], f"job:{job_id}:status")

    def test_job_poll_reads_through_the_app_client(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.v1.dependencies import resolve_api_key
        from app.api.v1.endpoints import jobs
        from app.schemas.job import JobResultResponse, JobStatus
        from app.services.job_service import JobService

        app = FastAPI()
        app.include_router(jobs.router)
        app.state.redis = shared = mock.AsyncMock()
        app.dependency_overrides[resolve_api_key] = lambda: SimpleNamespace(
            application_id=uuid.uuid4()
        )
        job_svc = mock.AsyncMock(spec=JobService)
        job_svc.get_result.return_value = JobResultResponse(
            job_id="job-1", status=JobStatus.PENDING, created_at=datetime.now(UTC)
        )

        with (
            mock.patch("app.services.job_service.JobService", return_value=job_svc) as service,
            mock.patch("redis.asyncio.from_url", return_value=mock.AsyncMock()) as from_url,
        ):
            response = TestClient(app).get("/v1/jobs/job-1")

        self.assertEqual(response.status_code, 200)
        from_url.assert_not_called()
        service.assert_called_once_with(shared)
        shared.aclose.assert_not_awaited()
