"""Proxy management endpoints — backed by ProxyManager service."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import SCOPE_ADMIN, require_scope, resolve_api_key
from app.core.db import get_db
from app.models.api_key import ApiKey
from app.models.proxy import Proxy
from app.models.proxy_pool import ProxyPool
from app.schemas.proxy import (
    PoolStatsResponse,
    ProxyBulkImport,
    ProxyHealthUpdate,
    ProxyImportResponse,
    ProxyPoolResponse,
    ProxyResponse,
)
from app.services.policy_resolver import normalize_domain

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/proxy", tags=["proxy"])


@router.get("/pools", response_model=list[ProxyPoolResponse])
async def list_pools(
    _api_key: ApiKey = Depends(resolve_api_key),
    db: AsyncSession = Depends(get_db),
):
    """List all active proxy pools."""
    result = await db.execute(select(ProxyPool).where(ProxyPool.is_active.is_(True)))
    return result.scalars().all()


@router.get("/pools/{pool_id}/stats", response_model=PoolStatsResponse)
async def get_pool_stats(
    pool_id: UUID,
    request: Request,
    _api_key: ApiKey = Depends(resolve_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Return aggregated health statistics for a pool."""
    pool = await db.get(ProxyPool, pool_id)
    if pool is None:
        raise HTTPException(status_code=404, detail="Proxy pool not found")

    proxy_manager = request.app.state.proxy_manager
    return await proxy_manager.get_pool_stats(pool_id, db)


@router.get("/proxies", response_model=list[ProxyResponse])
async def list_proxies(
    request: Request,
    pool_id: UUID | None = Query(None),
    country: str | None = Query(None),
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """List proxies filtered by pool and country. Admin only —
    proxy URLs contain credentials and must never appear in responses."""
    stmt = select(Proxy)
    if pool_id is not None:
        stmt = stmt.where(Proxy.pool_id == pool_id)
    if country is not None:
        stmt = stmt.where(Proxy.country == country.upper()[:2])
    stmt = stmt.order_by(Proxy.health_score.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/health")
async def report_proxy_health(
    body: ProxyHealthUpdate,
    request: Request,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Record a proxy request outcome. Admin scope — prevents external
    callers from manipulating proxy health scores."""
    proxy_manager = request.app.state.proxy_manager
    await proxy_manager.report_result(
        proxy_id=body.proxy_id,
        domain=body.domain,
        success=body.success,
        reason=body.reason,
        db=db,
    )
    await db.commit()
    return {"status": "recorded"}


@router.post("/reset/{proxy_id}")
async def reset_proxy(
    proxy_id: UUID,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Reset a proxy's health score to 1.0 and clear cooldown."""
    proxy = await db.get(Proxy, proxy_id)
    if proxy is None:
        raise HTTPException(status_code=404, detail="Proxy not found")

    proxy.health_score = 1.0
    proxy.consecutive_failures = 0
    proxy.cooldown_until = None
    await db.commit()
    return {"status": "reset", "proxy_id": str(proxy_id)}


@router.delete("/circuit-breaker/{domain}")
async def reset_circuit_breaker(
    domain: str,
    request: Request,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
):
    """Reset the circuit breaker for a domain. Admin only."""
    proxy_manager = request.app.state.proxy_manager
    domain_norm = normalize_domain(domain)
    await proxy_manager._reset_circuit_breaker(domain_norm)
    return {"status": "reset", "domain": domain_norm}


# ── Admin: bulk import ────────────────────────────────────────────────────────


@router.post("/admin/proxies", status_code=201, response_model=ProxyImportResponse)
async def import_proxies(
    payload: ProxyBulkImport,
    db: AsyncSession = Depends(get_db),
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
):
    """Bulk import proxies in host:port:user:pass:country format.

    Each proxy is upserted by its URL.  Existing proxies (matched by URL)
    are reactivated; new proxies are inserted into the tenant's pool.
    """
    from uuid import uuid4

    from sqlalchemy.dialects.postgresql import insert

    pool_id = uuid4()
    created = 0

    for item in payload.proxies:
        proxy_url = f"http://{item.username}:{item.password}@{item.host}:{item.port}"
        stmt = (
            insert(Proxy)
            .values(
                pool_id=pool_id,
                url=proxy_url,
                country=item.country.upper()[:2],
                health_score=1.0,
                consecutive_failures=0,
            )
            .on_conflict_do_update(
                index_elements=["url"],
                set_={
                    "country": item.country.upper()[:2],
                    "health_score": 1.0,
                    "consecutive_failures": 0,
                    "cooldown_until": None,
                },
            )
        )
        await db.execute(stmt)
        created += 1

    await db.commit()
    return {"imported": created}
