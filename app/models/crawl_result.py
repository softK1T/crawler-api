from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Integer, Text, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base
import uuid


class CrawlResult(Base):
    __tablename__ = "crawl_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    project_id: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    batch_id: Mapped[Optional[str]] = mapped_column(String(36), index=True, nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    response_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_encoding: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    markdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extracted: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    headers_trunc: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    crawled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<CrawlResult job_id={self.job_id} url={self.url} status={self.status_code}>"
