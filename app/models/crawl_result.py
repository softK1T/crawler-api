import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class CrawlResult(Base):
    __tablename__ = "crawl_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_encoding: Mapped[str | None] = mapped_column(String(32), nullable=True)
    markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    headers_trunc: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    crawled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return f"<CrawlResult job_id={self.job_id} url={self.url} status={self.status_code}>"
