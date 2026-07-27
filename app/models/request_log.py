import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RequestLog(Base):
    __tablename__ = "request_log"
    __table_args__ = (
        # Primary key is composite: (id, requested_at) — required for partitioned tables.
        # PARTITION BY RANGE (requested_at) is applied via raw DDL in the Alembic migration;
        # Alembic cannot auto-generate postgresql_partition_by.
        Index("ix_reqlog_app_time", "application_id", "requested_at"),
        Index("ix_reqlog_domain_time", "domain", "requested_at"),
        Index("ix_reqlog_apikey_time", "api_key_id", "requested_at"),
        {"postgresql_partition_by": "RANGE (requested_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, primary_key=True)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="SET NULL"),
        nullable=True,
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(String(8), default="GET", nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proxy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("proxies.id", ondelete="SET NULL"),
        nullable=True,
    )
    engine: Mapped[str] = mapped_column(String(16), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bytes_received: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, primary_key=True
    )

    def __repr__(self) -> str:
        return f"<RequestLog id={self.id} domain={self.domain!r} at={self.requested_at}>"
