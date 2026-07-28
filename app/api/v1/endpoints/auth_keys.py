"""Key management endpoints: create, list, revoke API keys; tenant/app CRUD."""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import (
    ALL_SCOPES,
    SCOPE_ADMIN,
    SCOPE_KEYS,
    require_scope,
    resolve_api_key,
)
from app.core.db import get_db
from app.core.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.application import Application
from app.models.tenant import Tenant
from app.schemas.api_key import (
    ApiKeyCreate,
    ApiKeyCreateResponse,
    ApiKeyResponse,
    ApiKeyRevoke,
)
from app.schemas.tenant import (
    ApplicationCreate,
    ApplicationResponse,
    TenantCreate,
    TenantResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth-keys"])

# ── API Key management ───────────────────────────────────────────────────────


@router.post("/v1/keys", response_model=ApiKeyCreateResponse, status_code=201)
async def create_api_key(
    body: ApiKeyCreate,
    api_key: ApiKey = Depends(require_scope(SCOPE_KEYS)),
    db: AsyncSession = Depends(get_db),
):
    """Create a new API key for the specified application.

    The raw key is returned only once — it is never stored in plaintext.
    """
    # Validate scopes.
    for scope in body.scopes:
        if scope not in ALL_SCOPES:
            raise AuthorizationError(detail=f"Invalid scope: {scope}")

    # Verify application exists.
    app_result = await db.execute(select(Application).where(Application.id == body.application_id))
    application = app_result.scalar_one_or_none()
    if application is None:
        raise NotFoundError(detail="Application not found")

    # Generate key with prefix collision retry (max 2 attempts).
    for _attempt in range(2):
        raw_key, hashed_key = generate_api_key()
        prefix = raw_key[:8]

        # Check for prefix collision.
        stmt = select(ApiKey.id).where(ApiKey.prefix == prefix).limit(1)
        existing = await db.execute(stmt)
        if existing.first() is not None:
            if _attempt == 1:
                raise ConflictError(detail="Key prefix collision — retry")
            continue

        row = ApiKey(
            application_id=body.application_id,
            prefix=prefix,
            hashed_key=hashed_key,
            scopes=body.scopes,
            mode=body.mode,
            expires_at=body.expires_at,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)

        # DO NOT LOG raw_key — returned to caller exactly once.
        response = ApiKeyCreateResponse.model_validate(row)
        response.raw_key = raw_key
        return response

    raise ConflictError(detail="Key prefix collision — retry exhausted")


@router.get("/v1/keys", response_model=list[ApiKeyResponse])
async def list_api_keys(
    api_key: ApiKey = Depends(resolve_api_key),
    db: AsyncSession = Depends(get_db),
):
    """List all API keys belonging to the caller's application."""
    stmt = (
        select(ApiKey)
        .where(ApiKey.application_id == api_key.application_id)
        .order_by(ApiKey.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.delete("/v1/keys/{key_id}", response_model=ApiKeyResponse)
async def revoke_api_key(
    key_id: UUID,
    body: ApiKeyRevoke | None = None,
    api_key: ApiKey = Depends(require_scope(SCOPE_KEYS)),
    db: AsyncSession = Depends(get_db),
):
    """Revoke an API key belonging to the caller's application.

    A key cannot revoke itself.
    """
    # Prevent self-revocation.
    if key_id == api_key.id:
        raise AuthorizationError(detail="Cannot revoke the key used to authenticate this request")

    stmt = select(ApiKey).where(
        ApiKey.id == key_id,
        ApiKey.application_id == api_key.application_id,
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        raise NotFoundError(detail="API key not found")

    row.revoked_at = datetime.now(UTC)
    row.is_active = False
    await db.commit()
    await db.refresh(row)

    if body and body.reason:
        logger.info(
            "Key revoked: id=%s prefix=%s reason=%s",
            row.id,
            row.prefix,
            body.reason,
        )

    return row


# ── Tenant & Application management ──────────────────────────────────────────


@router.post("/v1/tenants", response_model=TenantResponse, status_code=201)
async def create_tenant(
    body: TenantCreate,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Create a new tenant."""
    existing = await db.execute(select(Tenant).where(Tenant.name == body.name))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(detail="Tenant name already exists")

    row = Tenant(name=body.name)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/v1/applications", response_model=ApplicationResponse, status_code=201)
async def create_application(
    body: ApplicationCreate,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Create a new application under a tenant."""
    # Verify tenant exists and is active.
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == body.tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if tenant is None:
        raise NotFoundError(detail="Tenant not found")
    if not tenant.is_active:
        raise AuthorizationError(detail="Tenant is deactivated")

    # Check for duplicate name within tenant.
    existing = await db.execute(
        select(Application).where(
            Application.tenant_id == body.tenant_id,
            Application.name == body.name,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(detail="Application name already exists for this tenant")

    row = Application(tenant_id=body.tenant_id, name=body.name)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row
