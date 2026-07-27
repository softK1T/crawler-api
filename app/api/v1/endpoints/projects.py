"""Project endpoints — tenant + application management for admin users."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import SCOPE_ADMIN, require_scope
from app.core.db import get_db
from app.models.api_key import ApiKey
from app.models.tenant import Tenant
from app.schemas.tenant import TenantCreate, TenantResponse

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("/", response_model=TenantResponse, status_code=201)
async def create_project(
    body: TenantCreate,
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Create a new project (tenant)."""
    existing = await db.execute(select(Tenant).where(Tenant.name == body.name))
    if existing.scalar_one_or_none() is not None:
        from app.core.errors import ConflictError

        raise ConflictError(detail="Project name already exists")

    row = Tenant(name=body.name)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.get("/", response_model=list[TenantResponse])
async def list_projects(
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """List all projects (tenants)."""
    result = await db.execute(select(Tenant).order_by(Tenant.created_at.desc()))
    return result.scalars().all()
