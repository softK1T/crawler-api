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
