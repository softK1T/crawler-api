import math

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import SCOPE_FETCH, require_scope
from app.core.config import settings
from app.core.db import get_db
from app.core.ssrf_guard import validate_url_against_ssrf
from app.models.api_key import ApiKey
from app.schemas.requests import BatchCrawlRequest
from app.schemas.responses import BatchResponse, BatchStatusResponse
from app.services.batch_service import BatchService
from app.services.policy_resolver import normalize_domain

router = APIRouter(prefix="/batches", tags=["batches"])


@router.post("/", response_model=BatchResponse, status_code=202)
async def create_crawl_batch(
    request: BatchCrawlRequest,
    req: Request,
    api_key: ApiKey = Depends(require_scope(SCOPE_FETCH)),
    db: AsyncSession = Depends(get_db),
):
    # SSRF validation.
    if settings.ssrf_enabled:
        for url in request.urls:
            validate_url_against_ssrf(str(url))

    # Domain-level rate limiting: charge once per unique domain in the batch.
    rate_limiter = req.app.state.rate_limiter
    unique_domains = {normalize_domain(str(u)) for u in request.urls}
    for domain in unique_domains:
        result = await rate_limiter.check_domain(domain, rps=1.0)
        if not result["allowed"]:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limited",
                    "detail": f"Domain '{domain}' is rate-limited",
                    "retry_after": result["retry_after_s"],
                    "layer": result["layer"],
                },
                headers={"Retry-After": str(math.ceil(result["retry_after_s"]))},
            )

    # Note: BatchCrawlRequest may have fewer fields than the old request.
    # Fields not in the schema default to None.
    return BatchService.create_batch(
        urls=[str(url) for url in request.urls],
        mode=request.mode,
        project_id=None,
    )


@router.get("/{batch_id}/status", response_model=BatchStatusResponse)
async def get_batch_status(
    batch_id: str,
    api_key: ApiKey = Depends(require_scope(SCOPE_FETCH)),
):
    result = BatchService.get_batch_status(batch_id)
    if not result:
        raise HTTPException(status_code=404, detail="Batch not found")
    return result


@router.get("/{batch_id}/results")
async def get_batch_results(
    batch_id: str,
    api_key: ApiKey = Depends(require_scope(SCOPE_FETCH)),
):
    results = BatchService.get_batch_results(batch_id)
    if not results:
        raise HTTPException(status_code=404, detail="Batch not found")
    return results
