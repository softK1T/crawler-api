import math

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import SCOPE_FETCH, require_scope
from app.core.config import settings
from app.core.db import get_db
from app.core.url_guard import UrlNotAllowed, validate_url_async
from app.models.api_key import ApiKey
from app.schemas.requests import CrawlRequest
from app.schemas.responses import CrawlResult, JobResponse, JobStatusResponse
from app.services.job_service import JobService
from app.services.policy_resolver import get_policy_defaults, resolve_policy

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/", response_model=JobResponse, status_code=202)
async def create_crawl_job(
    request: CrawlRequest,
    req: Request,
    api_key: ApiKey = Depends(require_scope(SCOPE_FETCH)),
    db: AsyncSession = Depends(get_db),
):
    # SSRF / URL validation.
    if settings.ssrf_enabled:
        try:
            await validate_url_async(str(request.url))
        except UrlNotAllowed as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Resolve domain policy and enforce rate limits.
    policy = await resolve_policy(str(request.url), db)
    defaults = get_policy_defaults()
    domain_rps = policy.rate_limit_rps if policy else defaults["rate_limit_rps"]

    rate_limiter = req.app.state.rate_limiter
    result = await rate_limiter.check_all(
        api_key_prefix=api_key.prefix,
        application_id=api_key.application_id,
        domain=str(request.url),
        proxy_id=None,
        domain_rps=domain_rps,
        monthly_quota=settings.default_monthly_quota,
    )

    if not result["allowed"]:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limited",
                "detail": "Rate limit exceeded",
                "retry_after": result["retry_after_s"],
                "layer": result["layer"],
            },
            headers={"Retry-After": str(math.ceil(result["retry_after_s"]))},
        )

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

    response = JSONResponse(
        status_code=202,
        content=JobResponse(job_id=job_id).model_dump(),
    )
    response.headers["X-RateLimit-Limit"] = str(result["limit"])
    response.headers["X-RateLimit-Remaining"] = str(result["remaining"])
    response.headers["X-RateLimit-Reset"] = str(result["reset_at_ms"] // 1000)
    return response


@router.get("/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    api_key: ApiKey = Depends(require_scope(SCOPE_FETCH)),
):
    return JobService.get_job_status(job_id)


@router.get("/{job_id}/result", response_model=CrawlResult)
async def get_job_result(
    job_id: str,
    api_key: ApiKey = Depends(require_scope(SCOPE_FETCH)),
):
    result = JobService.get_job_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")
    return result
