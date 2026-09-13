"""Per-attempt request_log persistence — integration tests against real PostgreSQL.

Every test runs the REAL fetch_with_retry retry loop with a stub transport
fetcher, the REAL persist_request_attempt against a testcontainers PostgreSQL,
and (where proxies are involved) the REAL ProxyManager against Redis +
PostgreSQL.  No mocked persistence.
"""

import asyncio
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.errors import ProxyPoolExhaustedError, ProxyPoolUnavailableError
from app.schemas.fetch import BlockReason
from app.services.fetchers.base import FetchError, FetchResult, fetch_with_retry
from app.worker.tasks.fetch_task import _build_attempt_recorder

URL = "https://example.com/page"
DOMAIN = "example.com"


class _StubFetcher:
    """FetcherProtocol stand-in: pops one response or exception per call."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def fetch(
        self,
        url,
        *,
        proxy=None,
        headers=None,
        timeout_s=30.0,
        follow_redirects=True,
        max_redirects=10,
    ):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        # Real fetchers stamp the selected proxy onto the result.
        if item.proxy_id is None and proxy is not None:
            item.proxy_id = proxy.id
        return item


def _policy(**overrides) -> SimpleNamespace:
    base = {
        "escalation_tier": 0,
        "tier_locked": False,
        "antibot_type": None,
        "proxy_type": None,
        "max_escalation_attempts": 12,
        "use_proxy": False,
        "proxy_country": None,
        "proxy_city": None,
        "proxy_pool_id": None,
        "max_retries": 3,
        "min_delay_ms": 0,
        "max_delay_ms": 0,
        "engine": "httpx",
        "header_profile": None,
        "sticky_session": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _job_id() -> str:
    return f"job-{uuid4().hex[:8]}"


async def _log_rows(db_session_factory, job_id: str) -> list:
    from sqlalchemy import select

    from app.models.request_log import RequestLog

    async with db_session_factory() as db:
        result = await db.execute(
            select(RequestLog)
            .where(RequestLog.job_id == job_id)
            .order_by(RequestLog.attempt_number)
        )
        return list(result.scalars().all())


async def _make_pool_and_proxy(db_session, *, pool=None, url=None, **overrides):
    from app.models.proxy import Proxy, ProxyType
    from app.models.proxy_pool import ProxyPool

    if pool is None:
        pool = ProxyPool(name=f"pool-{uuid4().hex[:8]}", provider="webshare")
        db_session.add(pool)
        await db_session.flush()
    proxy = Proxy(
        pool_id=pool.id,
        url=url or "http://user:supersecret@10.0.0.7:8080",
        country=overrides.get("country", "PL"),
        city=overrides.get("city", "Warsaw"),
        proxy_type=ProxyType(overrides.get("proxy_type", "datacenter")),
        provider="webshare",
        health_score=1.0,
        is_active=True,
    )
    db_session.add(proxy)
    await db_session.commit()
    await db_session.refresh(proxy)
    return pool, proxy


def _make_proxy_manager(db_session_factory, redis_client):
    from app.services.proxy_manager import ProxyManager

    return ProxyManager(db_session_factory=db_session_factory, redis_client=redis_client)


def _recorder(db_session_factory, job_id: str, **identity):
    return _build_attempt_recorder(
        db_session_factory,
        job_id=job_id,
        api_key_id=identity.get("api_key_id"),
        application_id=identity.get("application_id"),
        trace_id=identity.get("trace_id", job_id),
    )


# ── 1. Direct success ────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_direct_success_persists_exactly_one_row(
    db_session, db_session_factory, redis_client, monkeypatch
):
    import app.services.fetchers as _fetchers

    body = b"<html>ok</html>"
    stub = _StubFetcher(
        [FetchResult(url=URL, status_code=200, body=body, engine="httpx", elapsed_ms=12)]
    )
    monkeypatch.setattr(_fetchers, "get_fetcher", lambda engine, **kw: stub)
    job_id = _job_id()

    result = await fetch_with_retry(
        fetcher=stub,
        url=URL,
        policy=_policy(),
        attempt_recorder=_recorder(db_session_factory, job_id),
    )

    rows = await _log_rows(db_session_factory, job_id)
    assert len(rows) == 1
    row = rows[0]
    assert result.status_code == 200
    assert row.proxy_id is None
    assert row.outcome == "success"
    assert row.status_code == 200
    assert row.url == URL
    assert row.domain == DOMAIN
    assert row.engine == "httpx"
    assert row.attempt_number == 1
    assert row.tier_attempt_number == 1
    assert row.escalation_tier == 0
    assert row.duration_ms is not None and row.duration_ms >= 0
    assert row.bytes_received == len(body)
    assert row.blocked is False
    assert row.completed_at is not None
    assert result._request_log_id == row.id


# ── 2. Proxy success ─────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_proxy_success_persists_safe_metadata_and_counts_once(
    db_session, db_session_factory, redis_client, monkeypatch
):
    import app.services.fetchers as _fetchers

    _, proxy = await _make_pool_and_proxy(db_session)
    pm = _make_proxy_manager(db_session_factory, redis_client)
    stub = _StubFetcher(
        [FetchResult(url=URL, status_code=200, body=b"ok", engine="httpx", elapsed_ms=9)]
    )
    monkeypatch.setattr(_fetchers, "get_fetcher", lambda engine, **kw: stub)
    job_id = _job_id()

    result = await fetch_with_retry(
        fetcher=stub,
        url=URL,
        policy=_policy(use_proxy=True, proxy_type="datacenter", max_retries=1),
        proxy_manager=pm,
        db=db_session,
        attempt_recorder=_recorder(db_session_factory, job_id),
    )

    rows = await _log_rows(db_session_factory, job_id)
    assert len(rows) == 1
    row = rows[0]
    assert result.proxy_id == proxy.id
    assert row.proxy_id == proxy.id
    assert row.proxy_provider == "webshare"
    assert row.proxy_type == "datacenter"
    assert row.proxy_country == "PL"
    assert row.proxy_city == "Warsaw"
    assert row.proxy_pool_id == proxy.pool_id
    # Proxy URL and credentials never reach request_log.
    for value in (row.proxy_provider, row.proxy_type, row.proxy_country, row.proxy_city):
        assert "supersecret" not in (value or "")
        assert "10.0.0.7" not in (value or "")
    assert result._request_log_id == row.id

    # Aggregate lifetime counters updated exactly once.
    await db_session.refresh(proxy)
    assert proxy.total_requests == 1
    assert proxy.total_errors == 0


# ── 3. Block → rotate/escalate → success ─────────────────────────────────────


@pytest.mark.integration
async def test_block_then_escalate_then_success_writes_two_rows(
    db_session, db_session_factory, redis_client, monkeypatch
):
    import app.services.fetchers as _fetchers

    pool, p1 = await _make_pool_and_proxy(db_session)
    _, p2 = await _make_pool_and_proxy(
        db_session,
        pool=pool,
        url="http://user:supersecret@10.0.0.8:8080",
        country="DE",
        city="Berlin",
    )
    pm = _make_proxy_manager(db_session_factory, redis_client)
    # Deterministic rotation order — real picker would random.choose.
    monkeypatch.setattr(pm, "get_proxy", AsyncMock(side_effect=[p1, p2]))
    stub = _StubFetcher(
        [
            FetchResult(
                url=URL,
                status_code=403,
                body=b"cf",
                engine="httpx",
                blocked=True,
                block_reason=BlockReason.CLOUDFLARE,
                proxy_id=p1.id,
            ),
            FetchResult(url=URL, status_code=200, body=b"ok", engine="httpx", proxy_id=p2.id),
        ]
    )
    monkeypatch.setattr(_fetchers, "get_fetcher", lambda engine, **kw: stub)
    job_id = _job_id()

    result = await fetch_with_retry(
        fetcher=stub,
        url=URL,
        policy=_policy(use_proxy=True, proxy_type="datacenter", max_retries=1),
        proxy_manager=pm,
        db=db_session,
        attempt_recorder=_recorder(db_session_factory, job_id),
    )

    assert result.status_code == 200
    rows = await _log_rows(db_session_factory, job_id)
    assert len(rows) == 2
    first, second = rows
    assert (first.attempt_number, second.attempt_number) == (1, 2)
    assert first.outcome == "blocked"
    assert first.blocked is True
    assert first.block_reason == "cloudflare"
    assert first.proxy_id == p1.id
    assert first.escalation_tier == 0
    assert first.tier_attempt_number == 1
    assert second.outcome == "success"
    assert second.proxy_id == p2.id
    assert second.escalation_tier == 1
    assert second.tier_attempt_number == 1
    assert result._request_log_id == second.id
    assert result._tier_used == 1


# ── 4. Timeout → success ─────────────────────────────────────────────────────


@pytest.mark.integration
async def test_timeout_then_success_updates_proxy_health_once(
    db_session, db_session_factory, redis_client, monkeypatch
):
    import app.services.fetchers as _fetchers

    _, proxy = await _make_pool_and_proxy(db_session)
    pm = _make_proxy_manager(db_session_factory, redis_client)
    monkeypatch.setattr(pm, "get_proxy", AsyncMock(side_effect=[proxy, proxy]))
    stub = _StubFetcher(
        [
            TimeoutError("read timed out"),
            FetchResult(url=URL, status_code=200, body=b"ok", engine="httpx", proxy_id=proxy.id),
        ]
    )
    monkeypatch.setattr(_fetchers, "get_fetcher", lambda engine, **kw: stub)
    job_id = _job_id()

    result = await fetch_with_retry(
        fetcher=stub,
        url=URL,
        policy=_policy(use_proxy=True, proxy_type="datacenter"),
        proxy_manager=pm,
        db=db_session,
        attempt_recorder=_recorder(db_session_factory, job_id),
    )

    assert result.status_code == 200
    rows = await _log_rows(db_session_factory, job_id)
    assert len(rows) == 2
    first, second = rows
    assert first.outcome == "network_error"
    assert first.error_type == "TimeoutError"
    assert first.status_code is None
    assert first.proxy_id == proxy.id
    assert second.outcome == "success"
    assert second.proxy_id == proxy.id

    # Exactly one failure + one success hit the lifetime counters.
    await db_session.refresh(proxy)
    assert proxy.total_requests == 2
    assert proxy.total_errors == 1
    assert proxy.consecutive_failures == 0  # cleared by the success


# ── 5. Generic unexpected exception ──────────────────────────────────────────


@pytest.mark.integration
async def test_generic_exception_writes_row_and_counts_proxy_error(
    db_session, db_session_factory, redis_client, monkeypatch
):
    import app.services.fetchers as _fetchers

    _, proxy = await _make_pool_and_proxy(db_session)
    pm = _make_proxy_manager(db_session_factory, redis_client)
    monkeypatch.setattr(pm, "get_proxy", AsyncMock(side_effect=[proxy]))
    stub = _StubFetcher([RuntimeError("boom")])
    monkeypatch.setattr(_fetchers, "get_fetcher", lambda engine, **kw: stub)
    job_id = _job_id()

    # Original behavior preserved: retries exhausted → FetchError propagates.
    with pytest.raises(FetchError):
        await fetch_with_retry(
            fetcher=stub,
            url=URL,
            policy=_policy(use_proxy=True, proxy_type="datacenter", max_escalation_attempts=1),
            proxy_manager=pm,
            db=db_session,
            attempt_recorder=_recorder(db_session_factory, job_id),
        )

    rows = await _log_rows(db_session_factory, job_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == "internal_error"
    assert row.error_type == "RuntimeError"
    assert row.error == "boom"
    assert row.proxy_id == proxy.id

    # Generic exception paths now count against the proxy exactly once.
    await db_session.refresh(proxy)
    assert proxy.total_requests == 1
    assert proxy.total_errors == 1


# ── 6. Pool unavailable ──────────────────────────────────────────────────────


@pytest.mark.integration
async def test_pool_unavailable_writes_row_with_null_proxy(
    db_session, db_session_factory, redis_client, monkeypatch
):
    import app.services.fetchers as _fetchers

    # No proxies in the DB → get_proxy returns None.
    pm = _make_proxy_manager(db_session_factory, redis_client)
    stub = _StubFetcher([])  # never called
    monkeypatch.setattr(_fetchers, "get_fetcher", lambda engine, **kw: stub)
    job_id = _job_id()

    with pytest.raises(ProxyPoolUnavailableError):
        await fetch_with_retry(
            fetcher=stub,
            url=URL,
            policy=_policy(use_proxy=True, proxy_type="datacenter"),
            proxy_manager=pm,
            db=db_session,
            attempt_recorder=_recorder(db_session_factory, job_id),
        )

    rows = await _log_rows(db_session_factory, job_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.proxy_id is None
    assert row.outcome == "proxy_pool_empty"
    assert row.error_type == "ProxyPoolUnavailableError"
    assert row.status_code is None


# ── 7. Pool exhausted ────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_pool_exhausted_writes_terminal_row(
    db_session, db_session_factory, redis_client, monkeypatch
):
    import app.services.fetchers as _fetchers

    _, proxy = await _make_pool_and_proxy(db_session)
    pm = _make_proxy_manager(db_session_factory, redis_client)
    stub = _StubFetcher(
        [
            FetchResult(
                url=URL,
                status_code=403,
                body=b"ban",
                engine="httpx",
                blocked=True,
                block_reason=BlockReason.IP_BAN,
                proxy_id=proxy.id,
            ),
        ]
    )
    monkeypatch.setattr(_fetchers, "get_fetcher", lambda engine, **kw: stub)
    job_id = _job_id()

    with pytest.raises(ProxyPoolExhaustedError):
        await fetch_with_retry(
            fetcher=stub,
            url=URL,
            policy=_policy(use_proxy=True, proxy_type="datacenter"),
            proxy_manager=pm,
            db=db_session,
            attempt_recorder=_recorder(db_session_factory, job_id),
        )

    rows = await _log_rows(db_session_factory, job_id)
    outcomes = [row.outcome for row in rows]
    assert outcomes.count("blocked") == 1
    assert "proxy_pool_exhausted" in outcomes
    terminal = rows[-1]
    assert terminal.outcome == "proxy_pool_exhausted"
    assert terminal.proxy_id is None
    assert terminal.error_type == "ProxyPoolExhaustedError"


# ── 8. Recorder DB failure ───────────────────────────────────────────────────


@pytest.mark.integration
async def test_recorder_db_failure_returns_result_and_counts_metric(
    db_session, db_session_factory, redis_client, caplog, monkeypatch
):
    from prometheus_client import REGISTRY

    import app.services.fetchers as _fetchers

    _, proxy = await _make_pool_and_proxy(db_session)
    pm = _make_proxy_manager(db_session_factory, redis_client)
    monkeypatch.setattr(pm, "get_proxy", AsyncMock(side_effect=[proxy]))
    stub = _StubFetcher(
        [FetchResult(url=URL, status_code=200, body=b"ok", engine="httpx", elapsed_ms=7)]
    )
    monkeypatch.setattr(_fetchers, "get_fetcher", lambda engine, **kw: stub)
    job_id = _job_id()

    def _broken_factory():
        raise RuntimeError("connection refused host=10.9.9.9 port=5432")

    before = REGISTRY.get_sample_value("crawler_request_log_write_failures_total") or 0
    with caplog.at_level(logging.ERROR, logger="app.services.request_attempt_log"):
        result = await fetch_with_retry(
            fetcher=stub,
            url=URL,
            policy=_policy(use_proxy=True, proxy_type="datacenter", max_retries=1),
            proxy_manager=pm,
            db=db_session,
            attempt_recorder=_build_attempt_recorder(
                _broken_factory,
                job_id=job_id,
                api_key_id=None,
                application_id=None,
                trace_id=job_id,
            ),
        )
    after = REGISTRY.get_sample_value("crawler_request_log_write_failures_total") or 0

    # The crawl result survives; only the audit write failed.
    assert result.status_code == 200
    assert result._request_log_id is None
    assert after - before == 1
    assert "request_attempt_persist_failed" in caplog.text
    # Proxy credentials must not appear in logs, even on failure paths.
    assert "supersecret" not in caplog.text
    assert "10.0.0.7" not in caplog.text


# ── 8b. Real task cancellation ───────────────────────────────────────────────


@pytest.mark.integration
async def test_task_cancellation_persists_cancelled_row(
    db_session, db_session_factory, redis_client, monkeypatch
):
    """task.cancel() during a slow fetch → exactly one outcome=cancelled row.

    Regression guard: cleanup awaits must survive cancellation, otherwise
    ``docker compose restart worker`` silently loses the last attempt's audit.
    """
    import app.services.fetchers as _fetchers

    fetch_started = asyncio.Event()

    class _SlowFetcher:
        async def fetch(
            self,
            url,
            *,
            proxy=None,
            headers=None,
            timeout_s=30.0,
            follow_redirects=True,
            max_redirects=10,
        ):
            fetch_started.set()
            await asyncio.Event().wait()  # block until cancelled

    stub = _SlowFetcher()
    monkeypatch.setattr(_fetchers, "get_fetcher", lambda engine, **kw: stub)
    job_id = _job_id()

    task = asyncio.create_task(
        fetch_with_retry(
            fetcher=stub,
            url=URL,
            policy=_policy(),
            attempt_recorder=_recorder(db_session_factory, job_id),
        )
    )
    await fetch_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    rows = await _log_rows(db_session_factory, job_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == "cancelled"
    assert row.error_type == "CancelledError"
    assert row.status_code is None
    assert row.proxy_id is None
    assert row.completed_at is not None


@pytest.mark.integration
async def test_recorder_survives_recancellation_during_persist(
    db_session, db_session_factory, redis_client, monkeypatch
):
    """A second cancel while the finally block awaits the recorder must not
    kill the shielded persistence — the row still lands in PostgreSQL."""
    import app.services.fetchers as _fetchers

    fetch_started = asyncio.Event()
    recorder_started = asyncio.Event()
    release_recorder = asyncio.Event()

    class _SlowFetcher:
        async def fetch(
            self,
            url,
            *,
            proxy=None,
            headers=None,
            timeout_s=30.0,
            follow_redirects=True,
            max_redirects=10,
        ):
            fetch_started.set()
            await asyncio.Event().wait()  # block until cancelled

    job_id = _job_id()
    real_recorder = _recorder(db_session_factory, job_id)

    async def _slow_recorder(attempt):
        recorder_started.set()
        await release_recorder.wait()
        return await real_recorder(attempt)

    stub = _SlowFetcher()
    monkeypatch.setattr(_fetchers, "get_fetcher", lambda engine, **kw: stub)

    task = asyncio.create_task(
        fetch_with_retry(
            fetcher=stub,
            url=URL,
            policy=_policy(),
            attempt_recorder=_slow_recorder,
        )
    )
    await fetch_started.wait()
    task.cancel()
    await recorder_started.wait()  # finally reached; shielded recorder running
    task.cancel()  # re-cancel while the task awaits the shielded recorder
    await asyncio.sleep(0.02)
    release_recorder.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The shielded recorder persists in the background — poll until it lands.
    rows: list = []
    for _ in range(40):
        rows = await _log_rows(db_session_factory, job_id)
        if rows:
            break
        await asyncio.sleep(0.05)
    assert len(rows) == 1
    assert rows[0].outcome == "cancelled"


# ── 9. Backward compatibility ────────────────────────────────────────────────


@pytest.mark.integration
async def test_fetch_with_retry_without_recorder_still_works(db_session, monkeypatch):
    """Existing callers (and old queued jobs) do not pass attempt_recorder."""
    import app.services.fetchers as _fetchers

    stub = _StubFetcher([FetchResult(url=URL, status_code=200, body=b"ok", engine="httpx")])
    monkeypatch.setattr(_fetchers, "get_fetcher", lambda engine, **kw: stub)

    result = await fetch_with_retry(fetcher=stub, url=URL, policy=_policy())

    assert result.status_code == 200
    assert result._request_log_id is None


@pytest.mark.integration
async def test_fetch_with_retry_recorder_defaults_to_none():
    """The recorder parameter is optional (signature-level backward compat)."""
    import inspect

    from app.services.fetchers.base import fetch_with_retry as fwr

    params = inspect.signature(fwr).parameters
    assert params["attempt_recorder"].default is None


# ── 12a. Legacy api_key resolution ───────────────────────────────────────────


@pytest.mark.integration
async def test_resolve_api_key_id_prefers_explicit_uuid(db_session):
    from app.worker.tasks.fetch_task import _resolve_api_key_id

    api_key_uuid = uuid4()
    resolved = await _resolve_api_key_id(db_session, str(api_key_uuid), "zzzzzzzz")
    assert resolved == api_key_uuid


@pytest.mark.integration
async def test_resolve_api_key_id_falls_back_to_prefix(db_session, api_key_factory):
    from app.worker.tasks.fetch_task import _resolve_api_key_id

    _, key = await api_key_factory()

    assert await _resolve_api_key_id(db_session, None, key.prefix) == key.id
    # Malformed explicit id also falls back to the prefix lookup.
    assert await _resolve_api_key_id(db_session, "not-a-uuid", key.prefix) == key.id


@pytest.mark.integration
async def test_resolve_api_key_id_returns_none_when_unresolvable(db_session):
    from app.worker.tasks.fetch_task import _resolve_api_key_id

    assert await _resolve_api_key_id(db_session, None, "nope_nop") is None


# ── 12b. Migration up/down/up, default partition, view ───────────────────────


@pytest.mark.integration
async def test_migration_0008_roundtrip_default_partition_and_view(_postgres_dsn):
    import os
    from datetime import date

    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from alembic import command
    from app.core.config import settings

    engine = create_async_engine(_postgres_dsn, echo=False)
    try:
        # Fresh schema so alembic can create everything from scratch.
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))

        prev_url = str(settings.database_url)
        settings.database_url = _postgres_dsn
        cfg = Config("alembic.ini")
        cfg.set_main_option("script_location", os.path.abspath("alembic"))
        try:
            await asyncio.to_thread(command.upgrade, cfg, "head")

            async with engine.begin() as conn:
                cols = await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'request_log'"
                    )
                )
                names = {row[0] for row in cols}
                for col in (
                    "job_id",
                    "attempt_number",
                    "tier_attempt_number",
                    "escalation_tier",
                    "outcome",
                    "block_reason",
                    "error_type",
                    "proxy_provider",
                    "proxy_type",
                    "proxy_country",
                    "proxy_city",
                    "proxy_pool_id",
                    "completed_at",
                ):
                    assert col in names

                # DEFAULT partition accepts timestamps outside 2026/2027.
                await conn.execute(
                    text(
                        "INSERT INTO request_log (id, domain, url, engine, requested_at) "
                        "VALUES (gen_random_uuid(), 'future.example', "
                        "'https://future.example/x', 'httpx', '2030-06-01T00:00:00Z')"
                    )
                )
                default_rows = (
                    await conn.execute(text("SELECT count(*) FROM request_log_default"))
                ).scalar()
                assert default_rows == 1

                # proxy_usage_daily aggregates with FILTER clauses.
                pool_id = "00000000-0000-0000-0000-0000000000aa"
                proxy_id = "00000000-0000-0000-0000-0000000000bb"
                await conn.execute(
                    text(
                        "INSERT INTO proxy_pools (id, name, provider) "
                        "VALUES (:id, 'mig-test-pool', 'webshare')"
                    ),
                    {"id": pool_id},
                )
                await conn.execute(
                    text(
                        "INSERT INTO proxies (id, pool_id, url, country, proxy_type) "
                        "VALUES (:id, :pool, 'http://u:s@10.1.1.1:8080', 'PL', 'datacenter')"
                    ),
                    {"id": proxy_id, "pool": pool_id},
                )
                for outcome, blocked, status, dur, byt in (
                    ("success", False, 200, 120, 5000),
                    ("blocked", True, 403, 80, 300),
                    ("network_error", False, None, 4000, 0),
                ):
                    await conn.execute(
                        text(
                            "INSERT INTO request_log (id, domain, url, engine, proxy_id, "
                            "proxy_provider, proxy_type, proxy_country, outcome, status_code, "
                            "blocked, duration_ms, bytes_received, requested_at) VALUES "
                            "(gen_random_uuid(), 'view.example', 'https://view.example/', "
                            "'httpx', :pid, 'webshare', 'datacenter', 'PL', :outcome, :status, "
                            ":blocked, :dur, :byt, '2026-09-13T10:00:00Z')"
                        ),
                        {
                            "pid": proxy_id,
                            "outcome": outcome,
                            "status": status,
                            "blocked": blocked,
                            "dur": dur,
                            "byt": byt,
                        },
                    )
                view_rows = (
                    (await conn.execute(text("SELECT * FROM proxy_usage_daily"))).mappings().all()
                )
                assert len(view_rows) == 1
                view_row = view_rows[0]
                assert view_row["attempts"] == 3
                assert view_row["successes"] == 1
                assert view_row["blocked_attempts"] == 1
                assert view_row["failed_attempts"] == 1
                assert view_row["bytes_received"] == 5300
                assert float(view_row["average_duration_ms"]) == 1400.0
                assert view_row["usage_date"] == date(2026, 9, 13)
                assert view_row["proxy_id"] == uuid.UUID(proxy_id)
                assert view_row["domain"] == "view.example"
                assert view_row["engine"] == "httpx"

            # Downgrade: view/indexes/constraint/columns removed, but the
            # DEFAULT partition is DETACHED — the relation and its audit
            # rows must survive as a standalone table.
            await asyncio.to_thread(command.downgrade, cfg, "0007")

            async with engine.begin() as conn:
                cols = await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'request_log'"
                    )
                )
                names = {row[0] for row in cols}
                assert "job_id" not in names
                assert "outcome" not in names
                assert "attempt_number" not in names
                view = (
                    await conn.execute(text("SELECT to_regclass('proxy_usage_daily')"))
                ).scalar()
                assert view is None
                # Standalone table still exists, detached from the parent.
                default_part = (
                    await conn.execute(text("SELECT to_regclass('request_log_default')"))
                ).scalar()
                assert default_part is not None
                attached = (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM pg_inherits "
                            "WHERE inhrelid = 'request_log_default'::regclass "
                            "AND inhparent = 'request_log'::regclass"
                        )
                    )
                ).scalar()
                assert attached == 0
                # The out-of-year audit row is preserved.
                preserved = (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM request_log_default "
                            "WHERE domain = 'future.example'"
                        )
                    )
                ).scalar()
                assert preserved == 1

            # Re-upgrade succeeds and re-attaches the preserved table.
            await asyncio.to_thread(command.upgrade, cfg, "head")
            async with engine.begin() as conn:
                cols = await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'request_log'"
                    )
                )
                names = {row[0] for row in cols}
                assert "outcome" in names and "job_id" in names
                view = (
                    await conn.execute(text("SELECT to_regclass('proxy_usage_daily')"))
                ).scalar()
                assert view is not None
                attached = (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM pg_inherits "
                            "WHERE inhrelid = 'request_log_default'::regclass "
                            "AND inhparent = 'request_log'::regclass"
                        )
                    )
                ).scalar()
                assert attached == 1
                # The preserved row is visible through the parent again.
                via_parent = (
                    await conn.execute(
                        text("SELECT count(*) FROM request_log WHERE domain = 'future.example'")
                    )
                ).scalar()
                assert via_parent == 1
        finally:
            settings.database_url = prev_url  # type: ignore[assignment]
            _restore_app_logging()
    finally:
        await engine.dispose()


def _restore_app_logging() -> None:
    """Undo the logging damage alembic's env.py fileConfig() causes.

    fileConfig runs on every alembic command and, with its default
    ``disable_existing_loggers=True``, flips ``.disabled`` on every logger
    that existed beforehand — which would silently break caplog and the
    structlog bridge in every test that runs after this one.
    """
    import logging

    for logger in logging.root.manager.loggerDict.values():
        if isinstance(logger, logging.Logger):
            logger.disabled = False

    from app.core.logging_config import configure_logging

    configure_logging("INFO")
