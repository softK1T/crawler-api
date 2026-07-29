import json
import logging
from typing import Any

import redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.legacy_crawl_result import CrawlResult as CrawlResultModel

logger = logging.getLogger(__name__)


class StorageService:
    """Dual-write storage: PostgreSQL (permanent) + Redis (TTL cache)."""

    def __init__(self):
        self._redis = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )

    # ── Redis helpers (fast cache layer) ────────────────────────────────────

    def save_job_result(self, job_id: str, result_data: dict[str, Any]) -> None:
        key = f"job:{job_id}"
        self._redis.setex(name=key, time=settings.result_ttl_secs, value=json.dumps(result_data))

    def get_job_result(self, job_id: str) -> dict[str, Any] | None:
        key = f"job:{job_id}"
        raw = self._redis.get(key)
        return json.loads(raw) if raw else None

    def save_job_created_at(self, job_id: str, iso_timestamp: str) -> None:
        key = f"job_meta:{job_id}"
        self._redis.setex(name=key, time=settings.result_ttl_secs, value=iso_timestamp)

    def get_job_created_at(self, job_id: str) -> str | None:
        key = f"job_meta:{job_id}"
        return self._redis.get(key)

    def save_batch_info(self, batch_id: str, batch_info: dict[str, Any]) -> None:
        key = f"batch:{batch_id}"
        self._redis.setex(name=key, time=settings.result_ttl_secs, value=json.dumps(batch_info))

    def get_batch_info(self, batch_id: str) -> dict[str, Any] | None:
        key = f"batch:{batch_id}"
        raw = self._redis.get(key)
        return json.loads(raw) if raw else None

    # ── PostgreSQL helpers (async, permanent) ────────────────────────────────

    @staticmethod
    async def save_result_to_db(db: AsyncSession, result_data: dict[str, Any]) -> None:
        """Persist crawl result to PostgreSQL."""
        try:
            record = CrawlResultModel(
                job_id=result_data.get("job_id"),
                project_id=result_data.get("project_id"),
                batch_id=result_data.get("batch_id"),
                url=result_data.get("url"),
                status_code=result_data.get("status_code"),
                content_type=result_data.get("content_type"),
                response_time_ms=result_data.get("response_time_ms"),
                body=result_data.get("body"),
                body_encoding=result_data.get("body_encoding"),
                markdown=result_data.get("markdown"),
                extracted=result_data.get("extracted"),
                headers_trunc=result_data.get("headers_trunc"),
                error_type=result_data.get("error_type"),
                error_message=result_data.get("error_message"),
            )
            db.add(record)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.error("Failed to save result to PostgreSQL: %s", exc)
            raise

    @staticmethod
    async def get_result_from_db(db: AsyncSession, job_id: str) -> dict[str, Any] | None:
        """Fetch crawl result from PostgreSQL by job_id."""
        stmt = select(CrawlResultModel).where(CrawlResultModel.job_id == job_id)
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            return None
        return {
            "job_id": record.job_id,
            "project_id": record.project_id,
            "batch_id": record.batch_id,
            "url": record.url,
            "status_code": record.status_code,
            "content_type": record.content_type,
            "response_time_ms": record.response_time_ms,
            "body": record.body,
            "body_encoding": record.body_encoding,
            "markdown": record.markdown,
            "extracted": record.extracted,
            "headers_trunc": record.headers_trunc,
            "error_type": record.error_type,
            "error_message": record.error_message,
            "crawled_at": record.crawled_at.isoformat() if record.crawled_at else None,
        }


storage = StorageService()
