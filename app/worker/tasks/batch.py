from time import perf_counter
from typing import List

from celery import group

from app.services.storage import save_batch_info
from app.worker.celery_app import celery_app
from app.worker.tasks.fetch import fetch_page

@celery_app.task(bind=True)
def process_batch(self, urls: List[str], headers=None, timeout_s=10, batch_id=None):
    job_group = group(
        fetch_page.s(url, headers=headers, timeout_s=timeout_s, batch_id=batch_id)
        for url in urls
    )

    result = job_group.apply_async()

    batch_info = {
        "batch_id": batch_id,
        "group_id": result.id,
        "total_tasks": len(urls),
        "created_at": perf_counter()
    }
    save_batch_info(batch_id, batch_info)

    return {
        "batch_id": batch_id,
        "group_id": result.id,
        "total_tasks": len(urls)
    }
