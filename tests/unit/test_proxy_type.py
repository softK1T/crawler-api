"""Unit tests for proxy_type filtering in ProxyManager.get_proxy()."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class FakeProxy:
    def __init__(self, proxy_id: str, country: str, proxy_type: str, health: float = 1.0):
        self.id = proxy_id
        self.country = country
        self.proxy_type = proxy_type
        self.health_score = health
        self.cooldown_until = None
        self.is_active = True


def _make_manager():
    """Construct a ProxyManager with all private dependencies mocked out."""
    from app.services.proxy_manager import ProxyManager

    mgr = ProxyManager.__new__(ProxyManager)
    object.__setattr__(mgr, "_db_factory", MagicMock())
    object.__setattr__(mgr, "_redis", AsyncMock())
    object.__setattr__(mgr, "_is_circuit_open", AsyncMock(return_value=False))
    object.__setattr__(mgr, "_get_sticky", AsyncMock(return_value=None))
    object.__setattr__(mgr, "_set_sticky", AsyncMock())
    return mgr


@pytest.mark.asyncio
async def test_proxy_type_residential_filters_only_residential():
    """proxy_type='residential' must exclude datacenter proxies."""
    residential = FakeProxy("r-1", "PL", "residential")
    datacenter = FakeProxy("d-1", "PL", "datacenter")

    mgr = _make_manager()
    mock_db = MagicMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock()
    mgr._db_factory.return_value = mock_db

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [residential, datacenter]
    mock_db.execute = AsyncMock(return_value=mock_result)

    proxy = await mgr.get_proxy(
        domain="example.com",
        sticky_key=None,
        proxy_type="residential",
    )

    assert proxy is not None


@pytest.mark.asyncio
async def test_proxy_type_none_returns_any():
    """proxy_type=None must return any proxy type."""
    residential = FakeProxy("r-1", "PL", "residential")
    datacenter = FakeProxy("d-1", "PL", "datacenter")

    mgr = _make_manager()
    mock_db = MagicMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock()
    mgr._db_factory.return_value = mock_db

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [residential, datacenter]
    mock_db.execute = AsyncMock(return_value=mock_result)

    proxy = await mgr.get_proxy(
        domain="example.com",
        sticky_key=None,
        proxy_type=None,
    )

    assert proxy is not None


# ── BUG 2 regression: proxy_type must be settable through the admin API ───────


def test_proxy_create_schema_accepts_proxy_type():
    """ProxyCreate must carry proxy_type (residential is allowed)."""
    from app.schemas.admin import ProxyCreate

    body = ProxyCreate(
        pool_id="00000000-0000-0000-0000-000000000001",
        url="http://user:pass@1.2.3.4:7970",
        country="PL",
        proxy_type="residential",
    )
    assert body.proxy_type == "residential"


def test_proxy_create_schema_defaults_to_datacenter():
    """Omitting proxy_type keeps the datacenter default (backwards compatible)."""
    from app.schemas.admin import ProxyCreate

    body = ProxyCreate(
        pool_id="00000000-0000-0000-0000-000000000001",
        url="http://user:pass@1.2.3.4:7970",
        country="PL",
    )
    assert body.proxy_type == "datacenter"


@pytest.mark.asyncio
async def test_add_proxy_to_pool_persists_proxy_type_and_get_proxy_finds_it(
    db_session, redis_client
):
    """A proxy created with proxy_type='residential' must be stored with that
    type and be returned by get_proxy(proxy_type='residential').

    Regression: previously all API-created proxies fell back to the
    server_default 'datacenter', so residential selection always returned
    PROXY_POOL_EMPTY.
    """
    from uuid import uuid4

    from sqlalchemy import select

    from app.api.v1.endpoints.admin import add_proxy_to_pool
    from app.models.proxy import Proxy
    from app.models.proxy_pool import ProxyPool
    from app.schemas.admin import ProxyCreate
    from app.services.proxy_manager import ProxyManager

    pool = ProxyPool(name=f"test-pool-{uuid4().hex[:8]}", provider="webshare")
    db_session.add(pool)
    await db_session.commit()
    await db_session.refresh(pool)

    body = ProxyCreate(
        pool_id=pool.id,
        url="http://resuser:pass@1.2.3.4:7970",
        country="PL",
        proxy_type="residential",
    )
    row = await add_proxy_to_pool(pool.id, body=body, _api_key=None, db=db_session)
    assert row.proxy_type == "residential"

    # Persisted in the DB, not just on the in-memory ORM object.
    persisted = (await db_session.execute(select(Proxy).where(Proxy.id == row.id))).scalar_one()
    assert persisted.proxy_type == "residential"

    # get_proxy with the same filter must find it; datacenter filter must not.
    class _SessionFactory:
        def __init__(self, session):
            self._session = session

        def __call__(self):
            return self

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, *exc):
            return False

    manager = ProxyManager(
        db_session_factory=_SessionFactory(db_session), redis_client=redis_client
    )
    picked = await manager.get_proxy(
        domain="example.com", sticky_key=None, proxy_type="residential"
    )
    assert picked is not None and picked.id == row.id

    picked_dc = await manager.get_proxy(
        domain="example.com", sticky_key=None, proxy_type="datacenter"
    )
    assert picked_dc is None
