import uuid
import time
from typing import List

from fastapi import APIRouter, HTTPException
from celery.result import AsyncResult
from app.schemas.fetch import FetchRequest, SubmitResponse, BatchFetchRequest, BatchSubmitResponse
from app.schemas.job import JobStatus, ResultEnvelope
from app.worker.celery_app import celery_app
from app.worker.tasks.fetch import fetch_page
from app.services.storage import get_result, save_batch_info, get_batch_info

router = APIRouter(prefix="/v1", tags=["crawler"])


@router.post("/fetch", response_model=SubmitResponse, status_code=202)
def submit_fetch(req: FetchRequest):
    task = fetch_page.delay(str(req.url), headers=req.headers, timeout_s=req.timeout_s)
    return SubmitResponse(task_id=task.id)


@router.get("/jobs/{task_id}", response_model=JobStatus)
def job_status(task_id: str):
    res = AsyncResult(task_id, app=celery_app)
    return JobStatus(task_id=task_id, state=res.state)


@router.get("/jobs/{task_id}/results", response_model=ResultEnvelope)
def job_result(task_id: str):
    payload = get_result(task_id)
    if not payload:
        return ResultEnvelope(exists=False, payload=None)
    return ResultEnvelope(exists=True, payload=payload)


@router.post("/fetch/batch", response_model=BatchSubmitResponse, status_code=202)
def submit_batch_fetch(req: BatchFetchRequest):
    batch_id = str(uuid.uuid4())
    task_ids = []

    for url in req.urls:
        task = fetch_page.delay(
            str(url),
            headers=req.headers,
            timeout_s=req.timeout_s,
            batch_id=batch_id
        )
        task_ids.append(task.id)

    batch_info = {
        "batch_id": batch_id,
        "task_ids": task_ids,
        "created_at": time.time()
    }
    save_batch_info(batch_id, batch_info)

    return BatchSubmitResponse(
        batch_id=batch_id,
        task_ids=task_ids,
        total_urls=len(req.urls),
    )


@router.get("/batch/{batch_id}/status")
def batch_status(batch_id: str):
    batch_info = get_batch_info(batch_id)
    if not batch_info:
        raise HTTPException(status_code=404, detail="Batch not found")

    task_ids = batch_info.get("task_ids", [])

    tasks_status = []
    completed_count = 0

    for task_id in task_ids:
        res = AsyncResult(task_id, app=celery_app)
        task_info = {
            "task_id": task_id,
            "state": res.state,
            "result": res.result if res.state == "SUCCESS" else None
        }
        tasks_status.append(task_info)

        if res.state in ["SUCCESS", "FAILURE"]:
            completed_count += 1

    total = len(task_ids)

    return {
        "batch_id": batch_id,
        "total": total,
        "completed": completed_count,
        "progress": completed_count / total if total > 0 else 0,
        "tasks": tasks_status
    }


@router.get("/batch/{batch_id}/results")
def batch_results(batch_id: str):
    batch_info = get_batch_info(batch_id)
    if not batch_info:
        raise HTTPException(status_code=404, detail="Batch not found")

    task_ids = batch_info.get("task_ids", [])

    results = []
    successful = 0
    failed = 0

    for task_id in task_ids:
        payload = get_result(task_id)
        if payload:
            results.append(payload)
            if payload.get("error_type") is None:
                successful += 1
            else:
                failed += 1

    return {
        "batch_id": batch_id,
        "total": len(task_ids),
        "successful": successful,
        "failed": failed,
        "results": results
    }
