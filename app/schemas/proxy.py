from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ProxyResponse(BaseModel):
    """Public proxy representation — url field is intentionally EXCLUDED (contains credentials)."""

    id: UUID
    pool_id: UUID
    country: str | None
    health_score: float
    consecutive_failures: int
    cooldown_until: datetime | None
    total_requests: int
    total_errors: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ProxyPoolResponse(BaseModel):
    id: UUID
    name: str
    provider: str
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PoolStatsResponse(BaseModel):
    pool_id: str
    total: int
    active: int
    on_cooldown: int
    avg_health: float
    circuit_breakers_open: list[str]


class ProxyHealthUpdate(BaseModel):
    proxy_id: UUID
    success: bool
    reason: Literal["http_error", "timeout", "blocked", "captcha"] | None = None
    domain: str


class ProxyImportItem(BaseModel):
    """Single proxy entry for bulk import: host:port:user:pass:country fields."""

    host: str
    port: int
    username: str
    password: str
    country: str  # ISO 3166-1 alpha-2


class ProxyBulkImport(BaseModel):
    tenant_id: UUID
    proxies: list[ProxyImportItem]


class ProxyImportResponse(BaseModel):
    imported: int
