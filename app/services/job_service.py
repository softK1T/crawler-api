import logging
from datetime import datetime, timezone
from typing import Optional, Dict

from app.worker.tasks.crawl import crawl_page
from app.services.storage import storage
from app.schemas.responses import JobStatusResponse, CrawlResult

logger = logging.getLogger(__name__)


class JobService:
    @staticmethod
    def create_job(
        url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
        delay: float = 2.0,
        use_proxy: bool = True,
        countdown: float = 0,
        project_id: Optional[str] = None,
        extract: Optional[Dict[str, str]] = None,
        mode: str = "static",
        proxy_country: Optional[str] = None,
        wait_for: Optional[str] = None,
    ) -> str:
        task = crawl_page.apply_async(
            args=[url],
            kwargs={
                "headers": headers,
                "timeout": timeout,
                "delay": delay,
                "use_proxy": use_proxy,
                "project_id": project_id,
                "extract": extract,
                "mode": mode,
                "proxy_country": proxy_country,
                "wait_for": wait_for,
            },
            countdown=countdown,
        )
        storage.save_job_created_at(task.id, datetime.now(timezone.utc).isoformat())
        return task.id

    @staticmethod
    def get_job_status(job_id: str) -> JobStatusResponse:
        result = crawl_page.AsyncResult(job_id)
        created_at = storage.get_job_created_at(job_id)
        return JobStatusResponse(
            job_id=job_id,
            state=result.state,
            created_at=created_at,
        )

    @staticmethod
    def get_job_result(job_id: str) -> Optional[CrawlResult]:
        data = storage.get_job_result(job_id)
        if not data:
            return None
        return CrawlResult(**data)
