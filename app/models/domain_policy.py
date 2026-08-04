import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

_ANTIBOT_TYPES = (
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
)

_PROXY_TYPES = ("datacenter", "residential", "mobile", "isp")


class DomainPolicy(Base):
    __tablename__ = "domain_policies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    proxy_pool_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("proxy_pools.id", ondelete="SET NULL"),
        nullable=True,
    )
    engine: Mapped[str] = mapped_column(String(16), default="httpx", nullable=False)
    rate_limit_rps: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    min_delay_ms: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    max_delay_ms: Mapped[int] = mapped_column(Integer, default=2000, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    respect_robots: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    header_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sticky_session: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    use_proxy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    proxy_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    # ── Anti-bot escalation fields (migration 0005) ──────────────────────────
    # Which vendor's stack this domain uses. NULL = unknown/undetected.
    # Values: none|cloudflare|akamai|datadome|kasada|perimeterx|incapsula|aws_waf|custom_sea|custom_cn
    antibot_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Learned preferred proxy type for this domain. Stored as plain string to
    # avoid coupling to ProxyType enum in the model layer.
    # Values: datacenter|residential|mobile|isp
    proxy_type: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Current position in the escalation ladder (0 = cheapest/direct).
    # Auto-incremented by policy_learner on repeated blocks; never decremented
    # automatically unless de-escalation probe succeeds.
    escalation_tier: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # When True, escalation logic must not change escalation_tier.
    # Set manually by an operator via the admin API to pin a domain.
    tier_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Timestamp of the last successful fetch (tz-aware). Used by the learner
    # to decide when to attempt a de-escalation probe.
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The BlockReason value from the most recent blocked response.
    last_block_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Count of consecutive blocked responses at the current tier.
    # Reset to 0 on any success. Used to emit alerts when a domain is
    # permanently stuck (e.g. requires manual intervention or a new proxy type).
    consecutive_blocks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Hard ceiling on total attempts across all tiers in a single fetch job.
    # Default 12 allows traversal to tier 5 with MAX_ATTEMPTS_PER_TIER=2
    # and leaves 2 attempts for the final tier.
    max_escalation_attempts: Mapped[int] = mapped_column(Integer, default=12, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<DomainPolicy id={self.id} domain={self.domain!r} tier={self.escalation_tier}>"
