import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DomainPolicyCreate(BaseModel):
    domain: str = Field(..., min_length=3, max_length=253)
    proxy_pool_id: UUID | None = None
    engine: Literal["httpx", "curl_cffi", "playwright"] = "httpx"
    rate_limit_rps: float = Field(default=1.0, ge=0.1, le=100.0)
    min_delay_ms: int = Field(default=500, ge=0, le=60000)
    max_delay_ms: int = Field(default=2000, ge=0, le=60000)
    max_retries: int = Field(default=3, ge=0, le=10)
    respect_robots: bool = True
    header_profile: dict[str, str] | None = None
    sticky_session: bool = False


class DomainPolicyUpdate(BaseModel):
    proxy_pool_id: UUID | None = None
    engine: Literal["httpx", "curl_cffi", "playwright"] | None = None
    rate_limit_rps: float | None = Field(default=None, ge=0.1, le=100.0)
    min_delay_ms: int | None = Field(default=None, ge=0, le=60000)
    max_delay_ms: int | None = Field(default=None, ge=0, le=60000)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    respect_robots: bool | None = None
    header_profile: dict[str, str] | None = None
    sticky_session: bool | None = None
    is_active: bool | None = None


class DomainPolicyResponse(BaseModel):
    id: UUID
    domain: str
    proxy_pool_id: UUID | None
    engine: str
    rate_limit_rps: float
    min_delay_ms: int
    max_delay_ms: int
    max_retries: int
    respect_robots: bool
    header_profile: dict[str, str] | None
    sticky_session: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ProxyPoolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    provider: Literal["webshare", "custom", "residential"]


_PROXY_URL_RE = re.compile(r"^https?://[^@]+:[^@]+@[^:]+:\d+$")


class ProxyCreate(BaseModel):
    pool_id: UUID
    url: str
    country: str | None = Field(default=None, min_length=2, max_length=2)

    @field_validator("url")
    @classmethod
    def validate_proxy_url(cls, v: str) -> str:
        if not _PROXY_URL_RE.match(v):
            raise ValueError("Proxy URL must be in format http://user:pass@host:port")
        return v
