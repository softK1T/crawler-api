from celery.result import AsyncResult
from typing import Optional
from app.worker.celery_app import celery_app
from app.services.storage import get_result
from app.schemas.responses import JobStatusResponse, CrawlResult, TaskState
from app.worker.tasks.crawl import crawl_page


class JobService:
    @staticmethod
    def create_job(url: str, headers: Optional[dict] = None, timeout: int = 15) -> str:
        task = crawl_page.delay(url, headers=headers, timeout=timeout)
        return task.id

    @staticmethod
    def get_job_status(job_id: str) -> JobStatusResponse:
        result = AsyncResult(job_id, app=celery_app)
        return JobStatusResponse(
            job_id=job_id,
            state=TaskState(result.state)
        )

    @staticmethod
    def get_job_result(job_id: str) -> Optional[CrawlResult]:
        payload = get_result(job_id)
        if not payload:
            return None

        return CrawlResult(
            job_id=job_id,
            url=payload.get("url"),
            status_code=payload.get("status_code"),
            response_time_ms=payload.get("response_time_ms", 0),
            body=payload.get("body"),
            error=payload.get("error_message")
        )
