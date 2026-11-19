"""Minute cron for opt-in public candidate pools. No browser or shop requests."""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, text

from app.core.db import AsyncSessionLocal
from app.core.proxy_check_config import proxy_check_settings as cfg
from app.models.proxy import Proxy
from app.models.proxy_pool import ProxyPool
from app.services.proxy_candidates import PROVIDER, record_check, trace_country
from app.services.proxy_geo import check_database, fetch_trace_body, lookup_country
from app.services.proxy_geo_policy import country_code, parse_exit_ip, probe_endpoint

logger = logging.getLogger(__name__)


async def check_proxy_candidates(ctx: dict) -> dict:
    if not cfg.enabled:
        return {"status": "disabled"}
    try:
        probe_endpoint(cfg.endpoint)
        if cfg.database_path:
            await asyncio.to_thread(check_database, cfg.database_path)
    except Exception:
        logger.error("Proxy checker configuration invalid; no candidate state changed")
        return {"status": "configuration_error"}

    from app.core.config import settings

    try:
        async with asyncio.timeout(min(75, max(1, settings.worker_job_timeout_s - 10))):
            async with AsyncSessionLocal() as db:
                if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(714028312)")):
                    return {"status": "busy"}
                rows = (
                    await db.execute(
                        select(Proxy)
                        .join(ProxyPool, Proxy.pool_id == ProxyPool.id)
                        .where(
                            Proxy.provider == PROVIDER,
                            ProxyPool.provider == PROVIDER,
                            ProxyPool.is_active.is_(True),
                            Proxy.check_managed.is_(True),
                            Proxy.check_auto_activate.is_(True),
                            or_(Proxy.check_due_at.is_(None), Proxy.check_due_at <= func.now()),
                        )
                        .order_by(Proxy.check_due_at.asc().nullsfirst(), Proxy.id)
                        .limit(cfg.batch_size)
                        .with_for_update(of=Proxy, skip_locked=True)
                    )
                ).scalars().all()
                semaphore = asyncio.Semaphore(cfg.concurrency)

                async def probe(proxy):
                    async with semaphore:
                        try:
                            body = await fetch_trace_body(proxy.url, cfg.endpoint, cfg.timeout_s)
                            exit_ip = parse_exit_ip(body)
                        except Exception as exc:
                            return proxy, None, None, None, type(exc).__name__[:64], None
                        try:
                            if cfg.database_path:
                                country = await asyncio.to_thread(
                                    lookup_country, cfg.database_path, exit_ip
                                )
                                source = "geolite2-country"
                            else:
                                country = trace_country(body)
                                source = "cloudflare-trace"
                            country = country_code(country) if country else None
                            return proxy, exit_ip, country, source, None, None
                        except Exception as exc:
                            return proxy, exit_ip, None, None, None, type(exc).__name__[:64]

                tasks = [asyncio.create_task(probe(proxy)) for proxy in rows]
                try:
                    results = await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                now = datetime.now(UTC)
                for proxy, exit_ip, country, source, error, geo_error in results:
                    record_check(
                        proxy, now=now, ttl_s=cfg.ttl_s, retry_s=cfg.retry_s,
                        exit_ip=exit_ip, country=country, source=source,
                        error=error, geo_error=geo_error,
                    )
                await db.commit()
                return {"status": "processed", "count": len(rows)}
    except TimeoutError:
        logger.warning("Proxy checker pass timed out; transaction rolled back")
        return {"status": "timeout"}
