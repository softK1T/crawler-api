from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from app.core.security import verify_api_key
from app.core.url_guard import UrlNotAllowed, validate_url_async
from app.schemas.requests import CrawlRequest
from app.schemas.responses import CrawlResult, JobResponse, JobStatusResponse
from app.services.job_service import JobService

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/", response_model=JobResponse, status_code=202)
async def create_crawl_job(
    request: CrawlRequest,
    _api_key: str = Depends(verify_api_key),
):
    if settings.ssrf_enabled:
        try:
            await validate_url_async(str(request.url))
        except UrlNotAllowed as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
