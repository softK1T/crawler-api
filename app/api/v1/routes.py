from fastapi import APIRouter, HTTPException
from celery.result import AsyncResult
from app.schemas.fetch import FetchRequest, SubmitResponse
from app.schemas.job import JobStatus, ResultEnvelope
from app.worker.celery_app import celery_app
from app.worker.tasks.fetch import fetch_page
from app.services.storage import get_result

router = APIRouter(prefix="/v1", tags=["crawler"])

@router.post("/fetch", response_model=SubmitResponse, status_code=202)
def submit_fetch(req: FetchRequest):
    task = fetch_page.delay(req.url, headers=req.headers, timeout_s=req.timeout_s)
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
