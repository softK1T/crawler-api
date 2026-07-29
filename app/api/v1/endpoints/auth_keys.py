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
from app.services.key_service import create_api_key as mint_key
from app.services.key_service import rotate_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth-keys"])


def _admin_or_own_app(api_key: ApiKey, target_app_id: UUID) -> None:
    """Raise AuthorizationError if *api_key* is not admin and not in the target app."""
    if SCOPE_ADMIN in api_key.scopes:
        return
    if api_key.application_id != target_app_id:
        raise AuthorizationError(detail="Cannot operate on another application's keys")


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
    # 1. Every requested scope must be a known scope.
    for scope in body.scopes:
        if scope not in ALL_SCOPES:
            raise AuthorizationError(detail=f"Invalid scope: {scope}")

    # 2. D2 Constraint A — caller may only grant scopes it holds.
    if not set(body.scopes).issubset(set(api_key.scopes)):
        raise AuthorizationError(detail="Cannot grant scopes you do not hold")

    # 3. D2 Constraint B — granting keys scope requires admin.
    if SCOPE_KEYS in body.scopes and SCOPE_ADMIN not in api_key.scopes:
        raise AuthorizationError(detail="admin scope required to grant keys scope")

    # 4. D3 — cross-application issuance requires admin.
    is_cross_app = body.application_id != api_key.application_id
    if is_cross_app and SCOPE_ADMIN not in api_key.scopes:
        raise AuthorizationError(detail="Cannot issue keys for another application")

    # 5. Application must exist and be active.
    app_result = await db.execute(
        select(Application).where(Application.id == body.application_id)
    )
    application = app_result.scalar_one_or_none()
    if application is None:
        raise NotFoundError(detail="Application not found")
    if not application.is_active:
        raise AuthorizationError(detail="Application is deactivated")

    # Delegate to the single key-minting path.
    row, raw_key = await mint_key(
        db,
        application_id=body.application_id,
        scopes=body.scopes,
        mode=body.mode,
        issuer_key_id=api_key.id,
        expires_at=body.expires_at,
    )

    # Log issuance exactly once — never log the raw key.
    logger.info(
        "Key issued: issuer_key_id=%s target_application_id=%s scopes=%s prefix=%s",
        api_key.id,
        body.application_id,
        body.scopes,
        row.prefix,
    )

    response = ApiKeyCreateResponse.model_validate(row)
    response.raw_key = raw_key
    return response


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
    """Revoke an API key.

    A key cannot revoke itself.  A keys-only caller is confined to its own
    application; an admin may revoke across applications.
    """
    # Prevent self-revocation.
    if key_id == api_key.id:
        raise AuthorizationError(
            detail="Cannot revoke the key used to authenticate this request"
        )

    # Load the target key.
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError(detail="API key not found")

    # D3 — cross-application revocation requires admin.
    _admin_or_own_app(api_key, row.application_id)

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


@router.post("/v1/keys/{key_id}/rotate", response_model=ApiKeyCreateResponse, status_code=201)
async def rotate_api_key_endpoint(
    key_id: UUID,
    api_key: ApiKey = Depends(require_scope(SCOPE_KEYS)),
    db: AsyncSession = Depends(get_db),
):
    """Rotate an API key: mint a successor, set the old key's expiry.

    The old key remains valid for KEY_ROTATION_OVERLAP_HOURS so clients
    have time to swap.  The new raw key is returned exactly once.
    """
    # Load the target key first — for non-admin cross-tenant access, return
    # 404 to avoid leaking existence of other tenants' key IDs.
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise NotFoundError(detail="API key not found")

    # D3 — cross-application access requires admin.  Return 404, not 403,
    # when the caller is not admin and the key belongs to another app.
    if SCOPE_ADMIN not in api_key.scopes and api_key.application_id != target.application_id:
        raise NotFoundError(detail="API key not found")

    successor, raw_key = await rotate_api_key(
        db,
        key_id=key_id,
        issuer_key_id=api_key.id,
    )

    response = ApiKeyCreateResponse.model_validate(successor)
    response.raw_key = raw_key
    return response


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
