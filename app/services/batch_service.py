"""Batch service — enqueues a group of fetch jobs and aggregates their state.

Backed by the same arq JobService used by POST /v1/fetch: each batch URL
becomes a real fetch_task job; batch metadata lives in Redis via the
StorageService.  Status/results endpoints aggregate per-job state from the
standard ``job:{id}:status`` keys.
"""

import asyncio
import time
import uuid
from urllib.parse import urlparse

from app.core.errors import NotFoundError
from app.schemas.job import JobStatus
from app.schemas.responses import BatchResponse, BatchStatusResponse, JobStatusResponse, TaskState
from app.services.job_service import JobService
from app.services.policy_resolver import normalize_domain
from app.services.storage import storage

_STATE_MAP: dict[str, TaskState] = {
    "pending": TaskState.PENDING,
    "running": TaskState.STARTED,
    "completed": TaskState.SUCCESS,
    "failed": TaskState.FAILURE,
}
_TERMINAL_STATES = {TaskState.SUCCESS, TaskState.FAILURE}


class BatchService:
    @staticmethod
    async def create_batch(
        *,
        urls: list[str],
        mode: str,
        api_key,
        redis_client,
        callback_url: str | None = None,
        options: dict | None = None,
    ) -> BatchResponse:
        """Enqueue one fetch_task per URL and record batch metadata in Redis."""
        batch_id = str(uuid.uuid4())
        job_ids: list[str] = []
        job_svc = JobService(redis_client)

        for url in urls:
            domain = normalize_domain(urlparse(url).hostname or url)
            job_id = str(uuid.uuid4())
            await job_svc.enqueue(
                job_id=job_id,
                url=url,
                mode=mode,
                api_key=api_key,
                domain=domain,
                proxy_pool_id=None,
                callback_url=callback_url,
                options=options or {},
                trace_id=None,
            )
            job_ids.append(job_id)

        batch_info = {
            "batch_id": batch_id,
            "job_ids": job_ids,
            "created_at": time.time(),
            "total_count": len(urls),
        }
        await asyncio.to_thread(storage.save_batch_info, batch_id, batch_info)

        return BatchResponse(batch_id=batch_id, job_ids=job_ids, total_count=len(urls))

    @staticmethod
    async def get_batch_status(batch_id: str, redis_client) -> BatchStatusResponse | None:
        """Aggregate per-job status into a batch-level progress report."""
        batch_info = await asyncio.to_thread(storage.get_batch_info, batch_id)
        if not batch_info:
            return None

        job_svc = JobService(redis_client)
        jobs: list[JobStatusResponse] = []
        completed = 0

        for job_id in batch_info.get("job_ids", []):
            try:
                data = await job_svc.get_status_data(job_id)
                state = _STATE_MAP.get(data.get("status") or "", TaskState.FAILURE)
                created_at = data.get("created_at")
            except NotFoundError:
                state = TaskState.FAILURE
                created_at = None
            if state in _TERMINAL_STATES:
                completed += 1
            jobs.append(JobStatusResponse(job_id=job_id, state=state, created_at=created_at))

        total = len(jobs)
        progress = completed / total if total > 0 else 0

        return BatchStatusResponse(
            batch_id=batch_id,
            total=total,
            completed=completed,
            progress=progress,
            jobs=jobs,
        )

    @staticmethod
    async def get_batch_results(batch_id: str, redis_client) -> dict | None:
        """Collect finished job results for the batch."""
        batch_info = await asyncio.to_thread(storage.get_batch_info, batch_id)
        if not batch_info:
            return None

        job_svc = JobService(redis_client)
        results = []
        successful = 0
        failed = 0

        for job_id in batch_info.get("job_ids", []):
            try:
                result = await job_svc.get_result(job_id)
            except NotFoundError:
                continue
            if result.status == JobStatus.COMPLETED:
                successful += 1
            elif result.status == JobStatus.FAILED:
                failed += 1
            results.append(result.model_dump(mode="json"))

        return {
            "batch_id": batch_id,
            "total": len(batch_info.get("job_ids", [])),
            "successful": successful,
            "failed": failed,
            "results": results,
        }
