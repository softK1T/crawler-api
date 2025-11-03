from fastapi import APIRouter, HTTPException
from app.schemas.requests import CrawlRequest
from app.schemas.responses import JobResponse, JobStatusResponse, CrawlResult
from app.services.job_service import JobService

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/", response_model=JobResponse, status_code=202)
def create_crawl_job(request: CrawlRequest):
    job_id = JobService.create_job(
        url=str(request.url),
        headers=request.headers,
        timeout=request.timeout
    )
    return JobResponse(job_id=job_id)


@router.get("/{job_id}/status", response_model=JobStatusResponse)
def get_job_status(job_id: str):
    return JobService.get_job_status(job_id)


@router.get("/{job_id}/result", response_model=CrawlResult)
def get_job_result(job_id: str):
    result = JobService.get_job_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")
    return result
