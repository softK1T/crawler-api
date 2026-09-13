"""Integration tests for arq worker fetch_task — success, blocked, failure paths.

The weak "assertion-only" tests that previously proved nothing (string
comparisons, pure math, import checks) were replaced with real behavior
tests: real PostgreSQL for usage counters and request_log persistence, real
Redis for status/error keys.
"""

import pytest


def _make_ctx(redis_client, db_factory=None):
    """Minimal arq ctx: real Redis, stub settings, optional real db factory."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    if db_factory is None:
        # No-op session fallback for tests that patch fetch_with_retry and
        # never touch the database for real.
        @asynccontextmanager
        async def _db_factory():
            db = AsyncMock()
            scalar_result = MagicMock()
            scalar_result.scalar_one_or_none.return_value = None  # no policy / api key
            db.execute = AsyncMock(return_value=scalar_result)
            yield db

        db_factory = _db_factory

    return {
        "redis": redis_client,
        "db_factory": db_factory,
        "settings": SimpleNamespace(
            job_result_ttl_s=60,
            ssrf_enabled=False,
            warc_enabled=False,
            archive_enabled=False,
            callback_max_retries=1,
        ),
        "browser_pool": None,
    }


def _real_ctx(redis_client, db_session_factory, *, warc_storage=None, proxy_manager=None):
    ctx = _make_ctx(redis_client, db_factory=db_session_factory)
    ctx["warc_storage"] = warc_storage
    ctx["proxy_manager"] = proxy_manager
    return ctx


class _WarcStub:
    """Records archive() invocations without touching S3/MinIO."""

    def __init__(self) -> None:
        self.archive_calls: list = []

    async def archive(self, *, fetch_result, request_log_id, db, warc_body=None):
        from types import SimpleNamespace

        self.archive_calls.append((fetch_result, request_log_id))
        return SimpleNamespace(is_revisit=False)


@pytest.mark.integration
async def test_fetch_task_success_stores_result_in_redis(
    redis_client, db_session, db_session_factory, application_factory
):
    """fetch_task must itself write a terminal status to Redis on success.

    Regression guard: this test used to set the key by hand and assert it was
    readable, so fetch_task was never invoked at all.  It also runs without
    ``api_key_id`` — the legacy queued-job path — against a real PostgreSQL.
    """
    import json
    from unittest.mock import AsyncMock, patch
    from uuid import uuid4

    from app.services.fetchers.base import FetchResult
    from app.worker.tasks.fetch_task import fetch_task

    application = await application_factory()
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
            _real_ctx(redis_client, db_session_factory),
            job_id=job_id,
            url="https://example.com",
            mode="static",
            api_key_prefix="ck_test",
            application_id=str(application.id),
            domain="example.com",
            proxy_pool_id=None,
            callback_url=None,
            options={},
        )

    raw = await redis_client.get(f"job:{job_id}:status")
    assert raw is not None, "fetch_task wrote no status to Redis"
    payload = json.loads(raw)
    assert payload["status"] == "completed", f"Job left in non-terminal state: {payload}"


@pytest.mark.integration
async def test_fetch_task_success_persists_row_and_passes_id_to_archive(
    redis_client, db_session, db_session_factory, application_factory, monkeypatch
):
    """The final successful attempt's request_log id reaches WARC archive()."""
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from sqlalchemy import select

    import app.services.fetchers as _fetchers
    import app.services.policy_learner as _learner
    from app.models.request_log import RequestLog
    from app.services.fetchers.base import FetchResult
    from app.worker.tasks.fetch_task import fetch_task
    from tests.integration.test_request_attempt_observability import _StubFetcher

    application = await application_factory()
    warc = _WarcStub()
    url = "https://example.com/page"
    stub = _StubFetcher(
        [FetchResult(url=url, status_code=200, body=b"<html>ok</html>", engine="httpx")]
    )
    monkeypatch.setattr(_fetchers, "get_fetcher", lambda engine, **kw: stub)
    monkeypatch.setattr(_learner, "record_outcome", AsyncMock())

    job_id = f"job-warc-{uuid4().hex[:8]}"
    await fetch_task(
        _real_ctx(redis_client, db_session_factory, warc_storage=warc),
        job_id=job_id,
        url=url,
        mode="static",
        api_key_prefix="ck_test",
        application_id=str(application.id),
        domain="example.com",
        proxy_pool_id=None,
        callback_url=None,
        options={},
    )

    async with db_session_factory() as db:
        rows = (
            (await db.execute(select(RequestLog).where(RequestLog.job_id == job_id)))
            .scalars()
            .all()
        )
    assert len(rows) == 1, "exactly one attempt row must exist for a direct success"
    row = rows[0]
    assert row.outcome == "success"
    assert row.application_id == application.id
    assert row.trace_id == job_id

    assert len(warc.archive_calls) == 1
    _, request_log_id = warc.archive_calls[0]
    assert request_log_id is not None
    assert request_log_id == row.id


@pytest.mark.integration
async def test_fetch_task_blocked_final_logged_but_not_archived(
    redis_client, db_session, db_session_factory, application_factory, monkeypatch
):
    """A blocked final response persists a row but is never WARC-archived."""
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from sqlalchemy import select

    import app.services.fetchers as _fetchers
    import app.services.policy_learner as _learner
    from app.models.domain_policy import DomainPolicy
    from app.models.request_log import RequestLog
    from app.schemas.fetch import BlockReason
    from app.services.fetchers.base import FetchResult
    from app.services.proxy_manager import ProxyManager
    from app.worker.tasks.fetch_task import fetch_task
    from tests.integration.test_request_attempt_observability import (
        _make_pool_and_proxy,
        _StubFetcher,
    )

    application = await application_factory()
    pool, proxy = await _make_pool_and_proxy(db_session)
    db_session.add(
        DomainPolicy(
            domain="example.com",
            use_proxy=True,
            proxy_pool_id=pool.id,
            proxy_type="datacenter",
            engine="httpx",
            max_escalation_attempts=1,
            min_delay_ms=0,
            max_delay_ms=0,
        )
    )
    await db_session.commit()

    warc = _WarcStub()
    url = "https://example.com/page"
    stub = _StubFetcher(
        [
            FetchResult(
                url=url,
                status_code=403,
                body=b"denied",
                engine="httpx",
                blocked=True,
                block_reason=BlockReason.IP_BAN,
                proxy_id=proxy.id,
            )
        ]
    )
    monkeypatch.setattr(_fetchers, "get_fetcher", lambda engine, **kw: stub)
    monkeypatch.setattr(_learner, "record_outcome", AsyncMock())

    job_id = f"job-blocked-{uuid4().hex[:8]}"
    await fetch_task(
        _real_ctx(
            redis_client,
            db_session_factory,
            warc_storage=warc,
            proxy_manager=ProxyManager(
                db_session_factory=db_session_factory, redis_client=redis_client
            ),
        ),
        job_id=job_id,
        url=url,
        mode="static",
        api_key_prefix="ck_test",
        application_id=str(application.id),
        domain="example.com",
        proxy_pool_id=None,
        callback_url=None,
        options={},
    )

    assert warc.archive_calls == [], "blocked responses must not be archived"

    async with db_session_factory() as db:
        rows = (
            (await db.execute(select(RequestLog).where(RequestLog.job_id == job_id)))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == "blocked"
    assert row.blocked is True
    assert row.block_reason == "ip_ban"
    assert row.proxy_id == proxy.id
    assert row.status_code == 403


@pytest.mark.integration
async def test_failure_stores_error_in_redis(redis_client):
    """_set_error writes both the status and the error keys."""
    import json

    from app.worker.tasks.fetch_task import _set_error

    job_id = "test-job-failed"
    await redis_client.delete(f"job:{job_id}:status")
    await redis_client.delete(f"job:{job_id}:error")

    await _set_error(redis_client, job_id, "Connection timeout", 60)

    error = await redis_client.get(f"job:{job_id}:error")
    assert error == b"Connection timeout"
    payload = json.loads(await redis_client.get(f"job:{job_id}:status"))
    assert payload["status"] == "failed"


@pytest.mark.integration
async def test_usage_counter_upsert_increments_real_database(db_session, application_factory):
    """Two upserts must accumulate request_count and bytes on the same row."""
    from sqlalchemy import select

    from app.models.usage_counter import UsageCounter
    from app.worker.tasks.fetch_task import _upsert_usage

    application = await application_factory()

    await _upsert_usage(db_session, str(application.id), 1_000_000)
    await _upsert_usage(db_session, str(application.id), 500_000)

    row = (
        await db_session.execute(
            select(UsageCounter).where(UsageCounter.application_id == application.id)
        )
    ).scalar_one()
    assert row.request_count == 2
    assert row.bytes_received == 1_500_000
    assert row.cost_eur_cents > 0


@pytest.mark.integration
async def test_callback_delivery_invoked_on_completion():
    """_schedule_callback actually delivers the payload via deliver_callback."""
    import asyncio
    from unittest.mock import patch

    from app.worker.tasks.fetch_task import _schedule_callback

    delivered = []

    async def _fake_deliver(callback_url, payload, secret):
        delivered.append((callback_url, payload, secret))

    with patch("app.services.callback.deliver_callback", side_effect=_fake_deliver):
        _schedule_callback(
            job_id="job-1",
            status="completed",
            callback_url="http://cb.local/hook",
            result=None,
            secret="test-secret",
        )
        await asyncio.sleep(0.1)

    assert len(delivered) == 1
    url, payload, secret = delivered[0]
    assert url == "http://cb.local/hook"
    assert payload.job_id == "job-1"
    assert payload.status.value == "completed"
    assert secret == "test-secret"


@pytest.mark.integration
async def test_cancelled_error_marks_worker_shutdown(redis_client):
    """CancelledError inside fetch_task → Redis error 'Worker shutdown' + re-raise."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from uuid import uuid4

    from app.worker.tasks.fetch_task import fetch_task

    job_id = f"job-cancel-{uuid4().hex[:8]}"
    await redis_client.delete(f"job:{job_id}:status")
    await redis_client.delete(f"job:{job_id}:error")

    async def _cancel(*args, **kwargs):
        raise asyncio.CancelledError

    with (
        patch("app.services.fetchers.base.fetch_with_retry", new=_cancel),
        patch("app.services.policy_learner.record_outcome", new=AsyncMock()),
    ):
        with pytest.raises(asyncio.CancelledError):
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

    error = await redis_client.get(f"job:{job_id}:error")
    assert error == b"Worker shutdown"


@pytest.mark.integration
async def test_proxy_url_never_in_response():
    """Proxy response schemas must not expose proxy URL."""
    from app.schemas.proxy import ProxyResponse

    fields = ProxyResponse.model_fields
    assert "url" not in fields
