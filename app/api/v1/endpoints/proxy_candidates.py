"""Admin-only candidate import, pause/resume, results and statistics."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import SCOPE_ADMIN, require_scope
from app.core.db import get_db
from app.core.proxy_check_config import proxy_check_settings
from app.models.api_key import ApiKey
from app.models.proxy import Proxy
from app.models.proxy_pool import ProxyPool
from app.services.proxy_candidates import PROVIDER, normalize_candidate

router = APIRouter(prefix="/proxy/candidates", tags=["proxy-candidates"])


class CandidateImport(BaseModel):
    pool_id: UUID
    proxies: list[SecretStr] = Field(min_length=1, max_length=500)


class CandidateSwitch(BaseModel):
    enabled: bool


async def candidate_pool(db: AsyncSession, pool_id: UUID) -> ProxyPool:
    pool = await db.get(ProxyPool, pool_id)
    if pool is None:
        raise HTTPException(status_code=404, detail="Proxy pool not found")
    if pool.provider != PROVIDER or not pool.is_active:
        raise HTTPException(status_code=409, detail="Active public-candidates pool required")
    return pool


@router.post("/import", status_code=201)
async def import_candidates(
    body: CandidateImport,
    _key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    await candidate_pool(db, body.pool_id)
    normalized = []
    for index, value in enumerate(body.proxies):
        try:
            normalized.append(normalize_candidate(value.get_secret_value()))
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid candidate at index {index}; public IP URL required",
            ) from None
    created = 0
    for url in dict.fromkeys(normalized):
        result = await db.execute(
            insert(Proxy)
            .values(
                pool_id=body.pool_id,
                provider=PROVIDER,
                url=url,
                country=None,
                proxy_type="unknown",
                is_active=False,
                check_managed=True,
                check_auto_activate=True,
                check_status="pending",
            )
            .on_conflict_do_nothing(index_elements=["provider", "url"])
            .returning(Proxy.id)
        )
        if result.scalar_one_or_none() is not None:
            created += 1
    await db.commit()
    return {
        "pool_id": str(body.pool_id),
        "created": created,
        "duplicates": len(normalized) - created,
        "checker_enabled_in_api_config": proxy_check_settings.enabled,
    }


@router.patch("/{proxy_id}/enabled")
async def switch_candidate(
    proxy_id: UUID,
    body: CandidateSwitch,
    _key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    proxy = await db.scalar(select(Proxy).where(Proxy.id == proxy_id).with_for_update())
    if proxy is None or proxy.provider != PROVIDER or not proxy.check_managed:
        raise HTTPException(status_code=404, detail="Managed candidate not found")
    proxy.check_auto_activate = body.enabled
    proxy.is_active = False
    proxy.check_status = "pending" if body.enabled else "paused"
    proxy.check_due_at = None
    await db.commit()
    return {"id": str(proxy.id), "status": proxy.check_status}


@router.post("/pools/{pool_id}/recheck", status_code=202)
async def recheck_candidates(
    pool_id: UUID,
    _key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    await candidate_pool(db, pool_id)
    rows = (
        await db.execute(
            select(Proxy)
            .where(
                Proxy.pool_id == pool_id,
                Proxy.provider == PROVIDER,
                Proxy.check_managed.is_(True),
                Proxy.check_auto_activate.is_(True),
            )
            .with_for_update()
        )
    ).scalars().all()
    for proxy in rows:
        proxy.is_active = False
        proxy.check_status = "pending"
        proxy.check_due_at = None
    await db.commit()
    return {"queued": len(rows)}


@router.get("/pools/{pool_id}/stats")
async def candidate_stats(
    pool_id: UUID,
    _key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    await candidate_pool(db, pool_id)
    rows = (
        await db.execute(
            select(
                Proxy.country,
                Proxy.check_status,
                func.count().label("total"),
                func.count().filter(
                    Proxy.is_active.is_(True), Proxy.check_due_at > func.now()
                ).label("fresh_active"),
            )
            .where(Proxy.pool_id == pool_id, Proxy.provider == PROVIDER)
            .group_by(Proxy.country, Proxy.check_status)
            .order_by(Proxy.country.asc().nullslast(), Proxy.check_status)
        )
    ).all()
    return {"pool_id": str(pool_id), "groups": [dict(row._mapping) for row in rows]}


@router.get("/pools/{pool_id}/results")
async def candidate_results(
    pool_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    await candidate_pool(db, pool_id)
    rows = (
        await db.execute(
            select(
                Proxy.id, Proxy.country, Proxy.exit_ip, Proxy.country_source,
                Proxy.check_status, Proxy.check_error, Proxy.check_last_at,
                Proxy.check_due_at, Proxy.is_active, Proxy.check_auto_activate,
            )
            .where(Proxy.pool_id == pool_id, Proxy.provider == PROVIDER)
            .order_by(Proxy.id).offset(offset).limit(limit)
        )
    ).all()
    return {"items": [dict(row._mapping) for row in rows], "limit": limit, "offset": offset}
