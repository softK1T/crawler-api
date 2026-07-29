from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TenantCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=128)


class TenantResponse(BaseModel):
    id: UUID
    name: str
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class TenantListResponse(BaseModel):
    items: list[TenantResponse]
    total: int


class ApplicationCreate(BaseModel):
    tenant_id: UUID
    name: str = Field(..., min_length=2, max_length=128)


class ApplicationUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=128)
    owner_label: str | None = None
    is_active: bool | None = None


class ApplicationResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    is_active: bool
    created_at: datetime
    owner_label: str | None = None
    model_config = ConfigDict(from_attributes=True)


class ApplicationListResponse(BaseModel):
    items: list[ApplicationResponse]
    total: int
