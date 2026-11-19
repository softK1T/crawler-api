import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

#: Allowed outcome values — must stay in sync with the ck_request_log_outcome
#: CHECK constraint created by migration 0008.  VARCHAR + CHECK, never a PG
#: enum, so adding an outcome does not require enum surgery.
REQUEST_LOG_OUTCOMES: tuple[str, ...] = (
    "success",
    "blocked",
    "fetch_error",
    "network_error",
    "proxy_pool_empty",
    "proxy_pool_exhausted",
    "cancelled",
    "internal_error",
    # Server default for legacy rows predating migration 0008.
    "unknown",
)


class RequestLog(Base):
    """One row per transport attempt (retry-loop iteration), not per job.

    Never stores proxy URLs/credentials, request/response headers, bodies,
    cookies or Authorization values — only safe proxy metadata snapshots.
    """

    __tablename__ = "request_log"
    __table_args__ = (
        # Primary key is composite: (id, requested_at) — required for partitioned tables.
        # PARTITION BY RANGE (requested_at) is applied via raw DDL in the Alembic migration;
        # Alembic cannot auto-generate postgresql_partition_by.
        Index("ix_reqlog_app_time", "application_id", "requested_at"),
        Index("ix_reqlog_domain_time", "domain", "requested_at"),
        Index("ix_reqlog_apikey_time", "api_key_id", "requested_at"),
        Index("ix_reqlog_job_attempt", "job_id", "attempt_number"),
        Index("ix_reqlog_proxy_time", "proxy_id", "requested_at"),
        Index("ix_reqlog_outcome_time", "outcome", "requested_at"),
        CheckConstraint(
            "outcome IN (%s)" % ", ".join(f"'{outcome}'" for outcome in REQUEST_LOG_OUTCOMES),  # noqa: UP031
            name="ck_request_log_outcome",
        ),
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

    # ── Attempt-level observability (migration 0008) ───────────────────────────
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tier_attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    escalation_tier: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    block_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    proxy_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proxy_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    proxy_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    proxy_city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    proxy_pool_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<RequestLog id={self.id} domain={self.domain!r} at={self.requested_at}>"
