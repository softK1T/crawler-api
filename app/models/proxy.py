import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Proxy(Base):
    __tablename__ = "proxies"
    __table_args__ = (
        Index("ix_proxy_pool_health", "pool_id", "health_score"),
        Index("ix_proxy_cooldown", "cooldown_until"),
        Index("ix_proxy_country", "country"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    pool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("proxy_pools.id", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    health_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_requests: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_errors: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
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
        return f"<Proxy id={self.id} country={self.country!r} score={self.health_score}>"
