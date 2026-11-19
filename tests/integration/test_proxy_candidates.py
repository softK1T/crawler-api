"""Real Postgres/Redis; probes are mocked. Requires disposable test services."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select, text

from app.api.v1.endpoints.proxy_candidates import (
    CandidateImport,
    CandidateSwitch,
    import_candidates,
    recheck_candidates,
    switch_candidate,
)
from app.models.proxy import Proxy
from app.models.proxy_pool import ProxyPool
from app.services.proxy_candidates import PROVIDER
from app.services.proxy_manager import ProxyManager
from app.worker.tasks import proxy_candidates as worker

pytestmark = pytest.mark.integration


async def seed(db):
    pool = ProxyPool(name=f"candidates-{uuid4().hex}", provider=PROVIDER)
    db.add(pool)
    await db.commit()
    return pool


def setup_worker(monkeypatch, factory):
    monkeypatch.setattr(worker, "AsyncSessionLocal", factory)
    monkeypatch.setattr(worker.cfg, "enabled", True)
    monkeypatch.setattr(worker.cfg, "database_path", None)
    probe = AsyncMock(return_value=b"ip=8.8.4.4\nloc=PL\n")
    monkeypatch.setattr(worker, "fetch_trace_body", probe)
    return probe


async def test_import_dedup_worker_and_ttl(db_session, db_session_factory, monkeypatch):
    pool = await seed(db_session)
    body = CandidateImport(pool_id=pool.id, proxies=[SecretStr("http://8.8.8.8:3128")] * 2)
    result = await import_candidates(body, _key=None, db=db_session)
    assert (result["created"], result["duplicates"]) == (1, 1)
    p = await db_session.scalar(select(Proxy).where(Proxy.pool_id == pool.id))
    assert not p.is_active
    assert p.proxy_type == "unknown"
    probe = setup_worker(monkeypatch, db_session_factory)
    assert (await worker.check_proxy_candidates({}))["count"] == 1
    await db_session.refresh(p)
    assert p.is_active and p.country == "PL"
    assert p.exit_ip == "8.8.4.4"
    await worker.check_proxy_candidates({})
    assert probe.await_count == 1


async def test_pause_survives_reimport_and_recheck(db_session):
    pool = await seed(db_session)
    body = CandidateImport(pool_id=pool.id, proxies=[SecretStr("http://8.8.8.8:3128")])
    await import_candidates(body, _key=None, db=db_session)
    p = await db_session.scalar(select(Proxy).where(Proxy.pool_id == pool.id))
    await switch_candidate(p.id, CandidateSwitch(enabled=False), _key=None, db=db_session)
    result = await import_candidates(body, _key=None, db=db_session)
    assert result["created"] == 0
    assert (await recheck_candidates(pool.id, _key=None, db=db_session))["queued"] == 0
    await db_session.refresh(p)
    assert p.check_status == "paused" and not p.is_active


async def test_failure_does_not_touch_paid_proxy(db_session, db_session_factory, monkeypatch):
    pool = await seed(db_session)
    body = CandidateImport(pool_id=pool.id, proxies=[SecretStr("http://8.8.8.8:3128")])
    await import_candidates(body, _key=None, db=db_session)
    paid = Proxy(
        pool_id=pool.id, provider="webshare", url="http://1.1.1.1:3128",
        country="PL", is_active=True,
    )
    db_session.add(paid)
    await db_session.commit()
    probe = setup_worker(monkeypatch, db_session_factory)
    probe.side_effect = RuntimeError("secret credentials must not be saved")
    await worker.check_proxy_candidates({})
    await db_session.refresh(paid)
    assert paid.is_active and paid.country == "PL"
    p = await db_session.scalar(select(Proxy).where(Proxy.provider == PROVIDER))
    assert not p.is_active and p.check_error == "RuntimeError"


async def test_pass_lock(db_session, db_session_factory, monkeypatch):
    await seed(db_session)
    probe = setup_worker(monkeypatch, db_session_factory)
    await db_session.execute(text("SELECT pg_advisory_xact_lock(714028312)"))
    try:
        assert (await worker.check_proxy_candidates({}))["status"] == "busy"
        probe.assert_not_awaited()
    finally:
        await db_session.rollback()


async def test_unknown_country_and_sticky_filters(db_session, db_session_factory, redis_client):
    pool = await seed(db_session)
    now = datetime.now(UTC)
    p = Proxy(
        pool_id=pool.id, provider=PROVIDER, url="http://8.8.8.8:3128", country=None,
        proxy_type="unknown", is_active=True, check_managed=True, check_auto_activate=True,
        check_status="ready", check_last_at=now, check_due_at=now + timedelta(hours=1),
    )
    db_session.add(p)
    await db_session.commit()
    manager = ProxyManager(db_session_factory, redis_client)
    await manager._set_sticky("example.com", "test", p.id, 60)
    assert await manager._get_sticky("example.com", "test") == p.id
    picked = await manager.get_proxy(
        pool_id=pool.id, domain="example.com", sticky_key="test", country="PL",
    )
    assert picked is None
    picked = await manager.get_proxy(pool_id=pool.id, domain="example.com", sticky_key=None)
    assert picked.id == p.id
