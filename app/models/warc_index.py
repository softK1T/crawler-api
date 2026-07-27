import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class WarcIndex(Base):
    __tablename__ = "warc_index"
    __table_args__ = (
        Index("ix_warc_sha256", "sha256"),
        Index("ix_warc_url_time", "url", "captured_at"),
        Index("ix_warc_filename_offset", "warc_filename", "offset"),
        Index("ix_warc_captured_at", "captured_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    # Soft link — FK cannot cross partition boundaries on request_log.
    request_log_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    warc_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    length: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    is_revisit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<WarcIndex id={self.id} url={self.url!r} captured_at={self.captured_at}>"
