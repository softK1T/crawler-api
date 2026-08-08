from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select

from app.models.proxy import Proxy
from app.models.proxy_event import ProxyEvent
from app.models.proxy_pool import ProxyPool
from app.services.proxy_providers.base import ProxyProvider, RawProxy
from app.services.proxy_sync_service import ProxySyncService


class _Provider(ProxyProvider):
    def __init__(self, name: str, proxies: list[RawProxy]) -> None:
        self.name = name
        self._proxies = proxies

    async def fetch_proxies(self) -> list[RawProxy]:
        return self._proxies


@pytest.mark.asyncio
async def test_sync_upserts_by_provider_and_url_and_creates_one_pool_per_provider(
    db_session,
) -> None:
    @asynccontextmanager
    async def _db_factory():
        yield db_session

    shared_url = "http://proxy-host:8000"
    providers = [
        _Provider(
            "webshare",
            [RawProxy(url=shared_url, country="PL", proxy_type="datacenter")],
        ),
        _Provider(
            "second",
            [RawProxy(url=shared_url, country="DE", proxy_type="residential")],
        ),
    ]

    service = ProxySyncService(db_factory=_db_factory, providers=providers)
    await service.sync()

    proxy_rows = (await db_session.execute(select(Proxy).order_by(Proxy.provider))).scalars().all()
    assert len(proxy_rows) == 2
    assert {row.provider for row in proxy_rows} == {"webshare", "second"}

    pools = (
        (await db_session.execute(select(ProxyPool).order_by(ProxyPool.provider))).scalars().all()
    )
    assert len(pools) == 2
    assert pools[0].name == "second-pool"
    assert pools[1].name == "webshare-pool"
    assert all(pool.is_active for pool in pools)


@pytest.mark.asyncio
async def test_sync_preserves_health_and_tracks_activation_transitions(db_session) -> None:
    @asynccontextmanager
    async def _db_factory():
        yield db_session

    proxy_url = "http://another-proxy:9000"
    service = ProxySyncService(
        db_factory=_db_factory,
        providers=[
            _Provider("webshare", [RawProxy(url=proxy_url, country="PL", proxy_type="datacenter")])
        ],
    )
    await service.sync()

    proxy = (await db_session.execute(select(Proxy).where(Proxy.url == proxy_url))).scalar_one()
    original_id: UUID = proxy.id
    proxy.health_score = 0.33
    proxy.consecutive_failures = 3
    proxy.total_requests = 123
    proxy.total_errors = 45
    cooldown_mark = datetime.now(UTC)
    proxy.cooldown_until = cooldown_mark
    await db_session.commit()

    await service.sync()
    refreshed = (
        await db_session.execute(select(Proxy).where(Proxy.id == original_id))
    ).scalar_one()
    assert refreshed.country == "PL"
    assert refreshed.proxy_type == "datacenter"
    assert refreshed.health_score == pytest.approx(0.33)
    assert refreshed.consecutive_failures == 3
    assert refreshed.total_requests == 123
    assert refreshed.total_errors == 45
    assert refreshed.cooldown_until == cooldown_mark

    service_without_proxy = ProxySyncService(
        db_factory=_db_factory,
        providers=[_Provider("webshare", [])],
    )
    await service_without_proxy.sync()

    deactivated = (
        await db_session.execute(select(Proxy).where(Proxy.id == original_id))
    ).scalar_one()
    assert deactivated.is_active is False

    await service.sync()
    reactivated = (
        await db_session.execute(select(Proxy).where(Proxy.id == original_id))
    ).scalar_one()
    assert reactivated.is_active is True

    events = (
        (
            await db_session.execute(
                select(ProxyEvent.event_type)
                .where(ProxyEvent.proxy_id == original_id)
                .order_by(ProxyEvent.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert events == ["deactivated", "activated"]
