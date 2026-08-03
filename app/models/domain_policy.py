import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


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
        return f"<DomainPolicy id={self.id} domain={self.domain!r}>"
