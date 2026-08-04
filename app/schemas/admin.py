import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Allowed literal values — kept in sync with DomainPolicy model constants.
AntibotType = Literal[
    "none",
    "cloudflare",
    "akamai",
    "datadome",
    "kasada",
    "perimeterx",
    "incapsula",
    "aws_waf",
    "custom_sea",
    "custom_cn",
]
PolicyProxyType = Literal["datacenter", "residential", "mobile", "isp"]
EngineType = Literal["httpx", "curl_cffi", "playwright", "camoufox"]


class DomainPolicyCreate(BaseModel):
    domain: str = Field(..., min_length=3, max_length=253)
    proxy_pool_id: UUID | None = None
    engine: EngineType = "httpx"
    rate_limit_rps: float = Field(default=1.0, ge=0.1, le=100.0)
    min_delay_ms: int = Field(default=500, ge=0, le=60000)
    max_delay_ms: int = Field(default=2000, ge=0, le=60000)
    max_retries: int = Field(default=3, ge=0, le=10)
    respect_robots: bool = True
    header_profile: dict[str, str] | None = None
    sticky_session: bool = False
    # Anti-bot escalation (optional on create; learner fills in at runtime)
    antibot_type: AntibotType | None = None
    proxy_type: PolicyProxyType | None = None
    escalation_tier: int = Field(default=0, ge=0, le=6)
    tier_locked: bool = False
    max_escalation_attempts: int = Field(default=12, ge=1, le=50)


class DomainPolicyUpdate(BaseModel):
    proxy_pool_id: UUID | None = None
    engine: EngineType | None = None
    rate_limit_rps: float | None = Field(default=None, ge=0.1, le=100.0)
    min_delay_ms: int | None = Field(default=None, ge=0, le=60000)
    max_delay_ms: int | None = Field(default=None, ge=0, le=60000)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    respect_robots: bool | None = None
    header_profile: dict[str, str] | None = None
    sticky_session: bool | None = None
    is_active: bool | None = None
    # Anti-bot escalation overrides (operator/admin use)
    antibot_type: AntibotType | None = None
    proxy_type: PolicyProxyType | None = None
    escalation_tier: int | None = Field(default=None, ge=0, le=6)
    tier_locked: bool | None = None
    max_escalation_attempts: int | None = Field(default=None, ge=1, le=50)


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
    use_proxy: bool
    proxy_country: str | None
    is_active: bool
    # Anti-bot escalation fields
    antibot_type: str | None
    proxy_type: str | None
    escalation_tier: int
    tier_locked: bool
    last_success_at: datetime | None
    last_block_reason: str | None
    consecutive_blocks: int
    max_escalation_attempts: int
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
