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


class ApplicationCreate(BaseModel):
    tenant_id: UUID
    name: str = Field(..., min_length=2, max_length=128)


class ApplicationResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
