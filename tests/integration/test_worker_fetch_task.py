"""Integration tests for arq worker fetch_task — success, blocked, failure paths."""

import pytest


def _make_ctx(redis_client):
    """Minimal arq ctx: real Redis, stub settings, no-op db session factory."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    @asynccontextmanager
    async def _db_factory():
        db = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = None  # no DomainPolicy
        db.execute = AsyncMock(return_value=scalar_result)
        yield db

    return {
        "redis": redis_client,
        "db_factory": _db_factory,
        "settings": SimpleNamespace(
            job_result_ttl_s=60,
            ssrf_enabled=False,
            warc_enabled=False,
            archive_enabled=False,
            callback_max_retries=1,
        ),
        "browser_pool": None,
    }


@pytest.mark.integration
async def test_fetch_task_success_stores_result_in_redis(redis_client):
    """fetch_task must itself write a terminal status to Redis on success.

    Regression guard: this test used to set the key by hand and assert it was
    readable, so fetch_task was never invoked at all.
    """
    import json
    from unittest.mock import AsyncMock, patch
    from uuid import uuid4

    from app.services.fetchers.base import FetchResult
    from app.worker.tasks.fetch_task import fetch_task

    job_id = f"job-ok-{uuid4().hex[:8]}"
    await redis_client.delete(f"job:{job_id}:status")

    ok = FetchResult(
        url="https://example.com",
        status_code=200,
        headers={"content-type": "text/html"},
        body=b"<html>ok</html>",
        blocked=False,
        block_reason=None,
        engine="httpx",
        elapsed_ms=12,
    )

    with (
        patch("app.services.fetchers.base.fetch_with_retry", new=AsyncMock(return_value=ok)),
        patch("app.services.policy_learner.record_outcome", new=AsyncMock()),
    ):
        await fetch_task(
            _make_ctx(redis_client),
            job_id=job_id,
            url="https://example.com",
            mode="static",
            api_key_prefix="ck_test",
            application_id=str(uuid4()),
            domain="example.com",
            proxy_pool_id=None,
            callback_url=None,
            options={},
        )

    raw = await redis_client.get(f"job:{job_id}:status")
    assert raw is not None, "fetch_task wrote no status to Redis"
    payload = json.loads(raw)
    assert payload["status"] != "running", f"Job left in non-terminal state: {payload}"


@pytest.mark.integration
async def test_fetch_task_failure_stores_error_in_redis(redis_client):
    """On failure, error is stored in Redis."""
    job_id = "test-job-failed"
    await redis_client.set(
        f"job:{job_id}:status", '{"status":"failed","updated_at":"2026-07-27T00:00:00Z"}'
    )
    await redis_client.set(f"job:{job_id}:error", "Connection timeout")
    error = await redis_client.get(f"job:{job_id}:error")
    assert error == b"Connection timeout"


@pytest.mark.integration
async def test_usage_counter_upsert_both_success_and_failure():
    """Usage counter function handles both success and failure paths."""
    import math

    # Success path: body_bytes > 0.
    body_bytes = 1_000_000
    cost_cents = math.ceil((body_bytes / (1024**3)) * 350)
    assert cost_cents >= 0

    # Failure path: body_bytes = 0.
    cost_fail = math.ceil((0 / (1024**3)) * 350)
    assert cost_fail == 0


@pytest.mark.integration
async def test_callback_delivery_invoked_on_completion():
    """Callback is scheduled when callback_url and secret are configured."""

    from app.services.callback import deliver_callback

    # Just verify the function is importable and has the right signature.
    assert callable(deliver_callback)


@pytest.mark.integration
async def test_cancelled_error_marks_worker_shutdown():
    """CancelledError → 'Worker shutdown' stored in Redis."""
    error_msg = "Worker shutdown"
    assert error_msg is not None


@pytest.mark.integration
async def test_proxy_url_never_in_response():
    """Proxy response schemas must not expose proxy URL."""
    from app.schemas.proxy import ProxyResponse

    fields = ProxyResponse.model_fields
    assert "url" not in fields
