from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert

from app.models.proxy import Proxy
from app.models.proxy_event import ProxyEvent
from app.models.proxy_pool import ProxyPool
from app.services.proxy_providers.base import ProxyProvider, RawProxy


@dataclass(frozen=True)
class ProxySyncStats:
    provider: str
    fetched: int
    inserted: int
    updated: int
    activated: int
    deactivated: int


@dataclass(frozen=True)
class ProxySyncResult:
    providers: list[ProxySyncStats]


class ProxySyncService:
    def __init__(self, db_factory, providers: list[ProxyProvider]) -> None:
        self._db_factory = db_factory
        self._providers = providers

    async def sync(self) -> ProxySyncResult:
        stats: list[ProxySyncStats] = []
        for provider in self._providers:
            raw = await provider.fetch_proxies()
            stats.append(await self._sync_provider(provider, raw))
        return ProxySyncResult(providers=stats)

    async def _sync_provider(
        self, provider: ProxyProvider, raw_proxies: list[RawProxy]
    ) -> ProxySyncStats:
        normalized = {proxy.url: proxy for proxy in raw_proxies}
        fetched_urls = set(normalized.keys())
        now = datetime.now(UTC)

        async with self._db_factory() as db:
            pool = await self._ensure_provider_pool(db, provider.name)

            existing_stmt: Select = select(Proxy.id, Proxy.url, Proxy.is_active).where(
                Proxy.provider == provider.name
            )
            existing_rows = (await db.execute(existing_stmt)).all()
            existing = {row.url: row for row in existing_rows}

            if normalized:
                upsert_values = [
                    {
                        "pool_id": pool.id,
                        "provider": provider.name,
                        "url": proxy.url,
                        "country": proxy.country,
                        "proxy_type": proxy.proxy_type,
                        "is_active": True,
                    }
                    for proxy in normalized.values()
                ]

                upsert_stmt = insert(Proxy).values(upsert_values)
                upsert_stmt = upsert_stmt.on_conflict_do_update(
                    index_elements=["provider", "url"],
                    set_={
                        "country": upsert_stmt.excluded.country,
                        "proxy_type": upsert_stmt.excluded.proxy_type,
                        "updated_at": now,
                    },
                )
                await db.execute(upsert_stmt)
                await db.execute(
                    update(Proxy)
                    .where(
                        Proxy.provider == provider.name,
                        Proxy.url.in_(list(fetched_urls)),
                        Proxy.pool_id != pool.id,
                    )
                    .values(pool_id=pool.id, updated_at=now)
                )

            activated_urls = [
                url for url, row in existing.items() if not row.is_active and url in fetched_urls
            ]
            if activated_urls:
                await db.execute(
                    update(Proxy)
                    .where(Proxy.provider == provider.name, Proxy.url.in_(activated_urls))
                    .values(is_active=True, updated_at=now)
                )
                for url in activated_urls:
                    db.add(
                        ProxyEvent(
                            proxy_id=existing[url].id,
                            event_type="activated",
                            detail={"provider": provider.name, "url": url},
                        )
                    )

            deactivated_urls = [
                url for url, row in existing.items() if row.is_active and url not in fetched_urls
            ]
            if deactivated_urls:
                await db.execute(
                    update(Proxy)
                    .where(Proxy.provider == provider.name, Proxy.url.in_(deactivated_urls))
                    .values(is_active=False, updated_at=now)
                )
                for url in deactivated_urls:
                    db.add(
                        ProxyEvent(
                            proxy_id=existing[url].id,
                            event_type="deactivated",
                            detail={"provider": provider.name, "url": url},
                        )
                    )

            await db.commit()

        inserted = len([url for url in fetched_urls if url not in existing])
        updated = len([url for url in fetched_urls if url in existing])
        return ProxySyncStats(
            provider=provider.name,
            fetched=len(fetched_urls),
            inserted=inserted,
            updated=updated,
            activated=len(activated_urls),
            deactivated=len(deactivated_urls),
        )

    async def _ensure_provider_pool(self, db, provider_name: str) -> ProxyPool:
        pool_stmt: Select = (
            select(ProxyPool)
            .where(ProxyPool.provider == provider_name)
            .order_by(ProxyPool.created_at.asc())
            .limit(1)
        )
        result = await db.execute(pool_stmt)
        pool: ProxyPool | None = result.scalar_one_or_none()
        if pool is not None:
            return pool

        new_pool = ProxyPool(name=f"{provider_name}-pool", provider=provider_name, is_active=True)
        db.add(new_pool)
        await db.flush()
        return new_pool
