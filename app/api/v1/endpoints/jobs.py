from fastapi import APIRouter, HTTPException, Depends
from app.schemas.requests import CrawlRequest
from app.schemas.responses import JobResponse, JobStatusResponse, CrawlResult
from app.services.job_service import JobService
from app.core.ssrf_guard import validate_url_against_ssrf
from app.core.security import verify_api_key
from app.core.config import settings

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/", response_model=JobResponse, status_code=202)
async def create_crawl_job(
    request: CrawlRequest,
    _api_key: str = Depends(verify_api_key),
):
    if settings.ssrf_enabled:
        validate_url_against_ssrf(str(request.url))
    job_id = JobService.create_job(
        url=str(request.url),
        headers=request.headers,
        timeout=request.timeout,
        delay=request.delay,
        use_proxy=request.use_proxy,
        project_id=request.project_id,
        extract=request.extract,
        mode=request.mode,
        proxy_country=request.proxy_country,
        wait_for=request.wait_for,
    )
    return JobResponse(job_id=job_id)


@router.get("/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    _api_key: str = Depends(verify_api_key),
):
    return JobService.get_job_status(job_id)


@router.get("/{job_id}/result", response_model=CrawlResult)
async def get_job_result(
    job_id: str,
    _api_key: str = Depends(verify_api_key),
):
    result = JobService.get_job_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")
    return result
