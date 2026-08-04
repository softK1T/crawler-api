"""Admin endpoints — DomainPolicy and ProxyPool CRUD."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import SCOPE_ADMIN, require_scope
from app.core.db import get_db
from app.core.errors import AuthorizationError, ConflictError, NotFoundError
from app.models.api_key import ApiKey
from app.models.domain_policy import DomainPolicy
from app.models.proxy import Proxy
from app.models.proxy_pool import ProxyPool
from app.schemas.admin import (
    DomainPolicyCreate,
    DomainPolicyResponse,
    DomainPolicyUpdate,
    ProxyCreate,
    ProxyPoolCreate,
)
from app.schemas.proxy import ProxyPoolResponse, ProxyResponse
from app.services.policy_resolver import normalize_domain

router = APIRouter(prefix="/admin", tags=["admin"])

# ── Domain Policies ──────────────────────────────────────────────────────────


@router.post("/domain-policies", response_model=DomainPolicyResponse, status_code=201)
async def create_domain_policy(
    body: DomainPolicyCreate,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Create or update a domain policy (upsert)."""
    from app.services.policy_resolver import upsert_policy

    domain_norm = normalize_domain(body.domain)
    updates = body.model_dump(exclude={"domain"})
    await upsert_policy(domain_norm, updates, db)

    # Fetch the upserted row.
    stmt = select(DomainPolicy).where(DomainPolicy.domain == domain_norm)
    result = await db.execute(stmt)
    return result.scalar_one()


@router.get("/domain-policies", response_model=list[DomainPolicyResponse])
async def list_domain_policies(
    domain: str | None = None,
    is_active: bool | None = None,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """List domain policies with optional filters."""
    stmt = select(DomainPolicy)
    if domain is not None:
        stmt = stmt.where(DomainPolicy.domain.contains(domain))
    if is_active is not None:
        stmt = stmt.where(DomainPolicy.is_active.is_(is_active))
    stmt = stmt.order_by(DomainPolicy.domain.asc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/domain-policies/{policy_id}", response_model=DomainPolicyResponse)
async def get_domain_policy(
    policy_id: UUID,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(DomainPolicy, policy_id)
    if row is None:
        raise NotFoundError(detail="Domain policy not found")
    return row


@router.patch("/domain-policies/{policy_id}", response_model=DomainPolicyResponse)
async def update_domain_policy(
    policy_id: UUID,
    body: DomainPolicyUpdate,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(DomainPolicy, policy_id)
    if row is None:
        raise NotFoundError(detail="Domain policy not found")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return row

    for key, value in updates.items():
        setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/domain-policies/{policy_id}", status_code=204)
async def delete_domain_policy(
    policy_id: UUID,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(DomainPolicy, policy_id)
    if row is None:
        raise NotFoundError(detail="Domain policy not found")
    await db.delete(row)
    await db.commit()


# ── Escalation tier pinning ──────────────────────────────────────────────────


@router.post(
    "/domain-policies/{policy_id}/pin-tier",
    response_model=DomainPolicyResponse,
    summary="Pin or unpin a domain's escalation tier",
)
async def pin_escalation_tier(
    policy_id: UUID,
    tier: int | None = None,
    locked: bool = True,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Pin a domain to a specific escalation tier and lock auto-escalation.

    - **tier**: target tier 0-6; if omitted, keeps the current tier.
    - **locked**: set False to unlock (re-enable auto-escalation) without
      changing the tier.

    When *locked=True* the policy_learner will not auto-change the tier.
    """
    row = await db.get(DomainPolicy, policy_id)
    if row is None:
        raise NotFoundError(detail="Domain policy not found")
    if tier is not None:
        if not (0 <= tier <= 6):
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="tier must be 0-6")
        row.escalation_tier = tier
    row.tier_locked = locked
    await db.commit()
    await db.refresh(row)
    return row


# ── Proxy Pools ──────────────────────────────────────────────────────────────


@router.post("/proxy-pools", response_model=ProxyPoolResponse, status_code=201)
async def create_proxy_pool(
    body: ProxyPoolCreate,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(ProxyPool).where(ProxyPool.name == body.name))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(detail="Proxy pool name already exists")

    row = ProxyPool(name=body.name, provider=body.provider)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/proxy-pools/{pool_id}/proxies", response_model=ProxyResponse, status_code=201)
async def add_proxy_to_pool(
    pool_id: UUID,
    body: ProxyCreate,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    pool = await db.get(ProxyPool, pool_id)
    if pool is None:
        raise NotFoundError(detail="Proxy pool not found")
    if not pool.is_active:
        raise AuthorizationError(detail="Cannot add proxies to an inactive pool")

    row = Proxy(
        pool_id=pool_id, url=body.url, country=body.country.upper()[:2] if body.country else None
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row
