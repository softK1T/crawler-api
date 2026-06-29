import uuid
import time
from typing import List, Optional, Dict
from app.services.job_service import JobService
from app.services.storage import storage
from app.schemas.responses import BatchResponse, BatchStatusResponse, JobStatusResponse


class BatchService:
    @staticmethod
    def create_batch(
        urls: List[str],
        headers: Optional[dict] = None,
        timeout: int = 15,
        delay: float = 1.0,
        use_proxy: bool = True,
        project_id: Optional[str] = None,
        extract: Optional[Dict[str, str]] = None,
        mode: str = "static",
        proxy_country: Optional[str] = None,
    ) -> BatchResponse:
        batch_id = str(uuid.uuid4())
        job_ids = []

        for url in urls:
            job_id = JobService.create_job(
                url=url,
                headers=headers,
                timeout=timeout,
                delay=delay,
                use_proxy=use_proxy,
                countdown=0,
                project_id=project_id,
                extract=extract,
                mode=mode,
                proxy_country=proxy_country,
            )
            job_ids.append(job_id)

        batch_info = {
            "batch_id": batch_id,
            "job_ids": job_ids,
            "created_at": time.time(),
            "total_count": len(urls),
        }
        storage.save_batch_info(batch_id, batch_info)

        return BatchResponse(
            batch_id=batch_id,
            job_ids=job_ids,
            total_count=len(urls),
        )

    @staticmethod
    def get_batch_status(batch_id: str) -> Optional[BatchStatusResponse]:
        batch_info = storage.get_batch_info(batch_id)
        if not batch_info:
            return None

        job_ids = batch_info.get("job_ids", [])
        jobs = []
        completed = 0

        for job_id in job_ids:
            job_status = JobService.get_job_status(job_id)
            jobs.append(job_status)
            if job_status.state in ["SUCCESS", "FAILURE"]:
                completed += 1

        total = len(job_ids)
        progress = completed / total if total > 0 else 0

        return BatchStatusResponse(
            batch_id=batch_id,
            total=total,
            completed=completed,
            progress=progress,
            jobs=jobs,
        )

    @staticmethod
    def get_batch_results(batch_id: str) -> Optional[dict]:
        batch_info = storage.get_batch_info(batch_id)
        if not batch_info:
            return None

        job_ids = batch_info.get("job_ids", [])
        results = []
        successful = 0
        failed = 0

        for job_id in job_ids:
            result = JobService.get_job_result(job_id)
            if result:
                results.append(result)
                if result.error_type is None:
                    successful += 1
                else:
                    failed += 1

        return {
            "batch_id": batch_id,
            "total": len(job_ids),
            "successful": successful,
            "failed": failed,
            "results": results,
        }
