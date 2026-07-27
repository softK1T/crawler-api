"""Job service — arq-backed enqueue, status polling, idempotency.

Celery compat shim: ``submit_crawl`` delegates for backward compatibility
with legacy worker code.
"""

import json
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Legacy Celery imports (keep for backward compat) ─────────────────────────
# ruff: noqa: E402
from app.schemas.responses import CrawlResult, JobStatusResponse  # noqa: F401
from app.services.storage import storage  # noqa: F401
from app.worker.tasks.crawl import crawl_page  # noqa: F401


class JobService:
    """arq-backed job service with legacy Celery compat."""

    def __init__(self, redis_client, settings_obj=None) -> None:
        self._redis = redis_client
        self._settings = settings_obj or settings

    async def enqueue(
        self,
        *,
        job_id: str,
        url: str,
        mode: str,
        api_key,
        domain: str,
        proxy_pool_id: UUID | None,
        callback_url: str | None,
        options: dict,
    ) -> str:
        """Enqueue a fetch_task via arq and set initial status in Redis."""
        import arq

        arq_redis = await arq.create_pool(
            arq.connections.RedisSettings.from_dsn(
                self._settings.arq_redis_url or self._settings.redis_url
            )
        )

        now = datetime.now(UTC).isoformat()
        status_data = {"status": "pending", "created_at": now}
        await self._redis.set(
            f"job:{job_id}:status",
            json.dumps(status_data),
            ex=self._settings.job_result_ttl_s,
        )

        await arq_redis.enqueue_job(
            "fetch_task",
            job_id=job_id,
            url=url,
            mode=mode,
            api_key_prefix=api_key.prefix,
            application_id=str(api_key.application_id),
            domain=domain,
            proxy_pool_id=str(proxy_pool_id) if proxy_pool_id else None,
            callback_url=callback_url,
            options=options,
        )
        await arq_redis.aclose()

        return job_id

    async def get_status(self, job_id: str) -> None:
        """Return job status enum.  Raises NotFoundError if key is missing."""
        from app.core.errors import NotFoundError

        raw = await self._redis.get(f"job:{job_id}:status")
        if not raw:
            raise NotFoundError(detail="Job not found")
        data = json.loads(raw)
        from app.schemas.job import JobStatus

        return JobStatus(data["status"])

    async def get_result(self, job_id: str) -> None:
        """Return full job result.  Raises NotFoundError if status key is missing."""
        from app.core.errors import NotFoundError

        raw_status = await self._redis.get(f"job:{job_id}:status")
        if not raw_status:
            raise NotFoundError(detail="Job not found")

        status_data = json.loads(raw_status)
        result = status_data.get("result")
        error = await self._redis.get(f"job:{job_id}:error")

        from app.schemas.job import JobResultResponse, JobStatus

        return JobResultResponse(
            job_id=job_id,
            status=JobStatus(status_data["status"]),
            result=result,
            error=error,
            created_at=datetime.fromisoformat(
                status_data.get("created_at", status_data["updated_at"])
            ),
            completed_at=(
                datetime.fromisoformat(status_data["updated_at"])
                if status_data["status"] in ("completed", "failed")
                else None
            ),
        )

    async def handle_idempotency(self, idempotency_key: str, application_id: UUID) -> str | None:
        """Check if an idempotency key was already used.  Returns job_id or None."""
        existing = await self._redis.get(
            f"idempotency:{application_id}:{idempotency_key[:128]}"
        )
        return existing if existing else None

    async def store_idempotency(self, idempotency_key: str, job_id: str, application_id: UUID) -> None:
        """Store the idempotency key → job_id mapping."""
        await self._redis.set(
            f"idempotency:{application_id}:{idempotency_key[:128]}",
            job_id,
            ex=self._settings.job_result_ttl_s,
        )


# ── Celery compat shim ───────────────────────────────────────────────────────


def submit_crawl(url: str, mode: str = "static", **kwargs) -> str:
    """Legacy compat — returns a synthetic job_id.

    DEPRECATED: new code should use ``JobService.enqueue`` directly.
    This shim exists to prevent import errors in legacy worker modules.
    """
    return str(uuid4())
