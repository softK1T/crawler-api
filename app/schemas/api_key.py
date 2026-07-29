from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ApiKeyCreate(BaseModel):
    application_id: UUID
    scopes: list[str] = ["fetch"]
    mode: str = "live"  # "live" or "test"
    expires_at: datetime | None = None


class ApiKeyResponse(BaseModel):
    id: UUID
    prefix: str
    scopes: list[str]
    mode: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    application_id: UUID
    model_config = ConfigDict(from_attributes=True)


class ApiKeyCreateResponse(ApiKeyResponse):
    raw_key: str  # returned ONLY on creation, never stored, never logged


class ApiKeyRevoke(BaseModel):
    reason: str | None = None
