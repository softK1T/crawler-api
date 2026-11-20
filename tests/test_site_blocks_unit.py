"""Offline tests: a site block is scoped to its domain and engine, not to the whole proxy."""

import asyncio
import os
import unittest
import uuid
from types import SimpleNamespace
from typing import Any
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")


class _Redis:
    def __init__(self) -> None:
        self.zsets: dict[str, dict[str, float]] = {}
        self.values: dict[str, Any] = {}

    async def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.zsets.setdefault(key, {}).update(mapping)

    async def zremrangebyscore(self, key: str, low: float, high: float) -> None:
        members = self.zsets.get(key, {})
        for member in [m for m, score in members.items() if low <= score <= high]:
            del members[member]

    async def zrangebyscore(self, key: str, low: float, high: Any) -> list[bytes]:
        return [m.encode() for m, score in self.zsets.get(key, {}).items() if score >= low]

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def incr(self, key: str) -> int:
        self.values[key] = int(self.values.get(key, 0)) + 1
        return int(self.values[key])

    async def get(self, key: str) -> Any:
        return self.values.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    async def hincrby(self, key: str, field: str, amount: int) -> int:
        return 2


class _Proxy:
    def __init__(self, proxy_id: str) -> None:
        self.id = proxy_id
        self.country = "PL"
        self.city = None
        self.proxy_type = "datacenter"
        self.health_score = 1.0
        self.cooldown_until = None
        self.is_active = True


def _manager(proxies: list[_Proxy]) -> Any:
    from app.services.proxy_manager import ProxyManager

    manager: Any = ProxyManager.__new__(ProxyManager)
    db = mock.MagicMock()
    db.__aenter__ = mock.AsyncMock(return_value=db)
    db.__aexit__ = mock.AsyncMock(return_value=False)
    result = mock.MagicMock()
    result.scalars.return_value.all.return_value = proxies
    db.execute = mock.AsyncMock(return_value=result)
    manager._db_factory = mock.MagicMock(return_value=db)
    manager._redis = _Redis()
    return manager


def _report(manager: Any, reason: str, engine: str) -> tuple[Any, Any]:
    database = mock.AsyncMock()
    database.get.return_value = None
    with (
        mock.patch("app.services.proxy_health.record_failure", mock.AsyncMock()) as failure,
        mock.patch("app.services.proxy_health.record_site_block", mock.AsyncMock()) as site,
    ):
        asyncio.run(
            manager.report_result(
                proxy_id="p-1",
                domain="example.pl",
                success=False,
                reason=reason,
                db=database,
                engine=engine,
            )
        )
    return failure, site


def _pick(manager: Any, domain: str, engine: str) -> Any:
    return asyncio.run(
        manager.get_proxy(domain=domain, sticky_key=None, proxy_type="datacenter", engine=engine)
    )


class SiteBlockTests(unittest.TestCase):
    def test_site_block_gives_no_global_cooldown(self) -> None:
        manager = _manager([_Proxy("p-1")])
        failure, site = _report(manager, "cloudflare", "curl_cffi")
        failure.assert_not_awaited()
        site.assert_awaited_once()

    def test_transport_failure_still_cools_the_proxy_down(self) -> None:
        manager = _manager([_Proxy("p-1")])
        failure, site = _report(manager, "timeout", "curl_cffi")
        failure.assert_awaited_once()
        site.assert_not_awaited()
        self.assertIsNotNone(_pick(manager, "example.pl", "curl_cffi"))

    def test_block_applies_to_that_domain_and_engine_only(self) -> None:
        manager = _manager([_Proxy("p-1")])
        _report(manager, "cloudflare", "curl_cffi")
        self.assertIsNone(_pick(manager, "example.pl", "curl_cffi"))
        self.assertIsNotNone(_pick(manager, "example.pl", "camoufox"))
        self.assertIsNotNone(_pick(manager, "other.pl", "curl_cffi"))

    def test_expired_block_is_forgotten(self) -> None:
        from app.services import proxy_manager

        manager = _manager([_Proxy("p-1")])
        with mock.patch.object(proxy_manager, "SITE_BLOCK_TTL_S", -1):
            _report(manager, "cloudflare", "curl_cffi")
        self.assertIsNotNone(_pick(manager, "example.pl", "curl_cffi"))


class NextEngineTests(unittest.TestCase):
    def test_engine_without_a_usable_proxy_is_skipped(self) -> None:
        from app.core.config import settings
        from app.services.fetchers.base import FetchResult, fetch_with_retry

        engines: list[str] = []

        def get_fetcher(engine: str, **kwargs: Any) -> Any:
            async def fetch(url: str, **kw: Any) -> FetchResult:
                engines.append(engine)
                return FetchResult(url=url, status_code=200, engine=engine)

            return SimpleNamespace(_engine_name=engine, fetch=fetch)

        async def get_proxy(**kwargs: Any) -> Any:
            if kwargs["engine"] == "curl_cffi":
                return None
            return SimpleNamespace(id=uuid.uuid4(), country="PL")

        proxies: Any = SimpleNamespace(get_proxy=get_proxy, report_result=mock.AsyncMock())
        with (
            mock.patch("app.services.fetchers.get_fetcher", get_fetcher),
            mock.patch("asyncio.sleep", mock.AsyncMock()),
            mock.patch.object(settings, "enable_premium_proxy_tiers", False),
        ):
            result = asyncio.run(
                fetch_with_retry(
                    get_fetcher("curl_cffi"),
                    "https://www.example.pl/",
                    proxy_manager=proxies,
                    requested_engine="curl_cffi",
                )
            )
        self.assertEqual(engines, ["playwright"])
        self.assertEqual(result.engine, "playwright")


if __name__ == "__main__":
    unittest.main()
