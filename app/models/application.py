import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.api_key import ApiKey


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_application_tenant_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    owner_label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Back-populates ApiKey.application; lazy="raise" prevents N+1 queries.
    api_keys: Mapped[list["ApiKey"]] = relationship(
        "ApiKey", back_populates="application", lazy="raise"
    )

    def __repr__(self) -> str:
        return f"<Application id={self.id} name={self.name!r}>"
