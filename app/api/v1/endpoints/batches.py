import math

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import SCOPE_FETCH, require_scope
from app.core.config import settings
from app.core.db import get_db
from app.core.observability import RATE_LIMIT_HITS_TOTAL
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
    if len(request.urls) > settings.max_batch_size:
        raise HTTPException(
            status_code=422, detail=f"At most {settings.max_batch_size} URLs per batch"
        )
    if request.callback_url and not (settings.enable_callbacks and settings.callback_hmac_secret):
        raise HTTPException(status_code=422, detail="Callbacks are disabled")

    from app.services.fetchers.request_options import (
        RequestOptions,
        carries_credentials,
        spans_multiple_sites,
    )

    try:
        request_options = RequestOptions.from_options(dict(request.options or {}))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    # One cookie set must never be sent to several unrelated sites.
    if carries_credentials(request_options) and spans_multiple_sites(
        str(batch_url) for batch_url in request.urls
    ):
        raise HTTPException(
            status_code=422,
            detail="cookies and Authorization require all batch URLs on one registered domain",
        )

    # Each URL consumes per-key admission, not just one token per batch.
    limiter = req.app.state.rate_limiter
    admission = await limiter.check_key(
        api_key.prefix, limit=settings.key_rate_limit_rpm, cost=len(request.urls)
    )
    if not admission["allowed"]:
        RATE_LIMIT_HITS_TOTAL.labels(layer=admission["layer"]).inc()
        raise HTTPException(
            status_code=429,
            detail="Key rate limit exceeded",
            headers={"Retry-After": str(math.ceil(admission["retry_after_s"]))},
        )

    # SSRF validation.
    if settings.ssrf_enabled:
        from app.core.url_guard import URLGuardError, validate_url_async

        for url in request.urls:
            try:
                await validate_url_async(str(url))
            except URLGuardError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None

    # Domain-level rate limiting: charge once per unique domain in the batch.
    rate_limiter = req.app.state.rate_limiter
    unique_domains = {normalize_domain(str(u)) for u in request.urls}
    from app.services.policy_resolver import resolve_policy

    for domain in unique_domains:
        _policy = await resolve_policy(f"https://{domain}", db)
        _rps = float(getattr(_policy, "rate_limit_rps", None) or 1.0)
        result = await rate_limiter.check_domain(domain, rps=_rps)
        if not result["allowed"]:
            RATE_LIMIT_HITS_TOTAL.labels(layer=result["layer"]).inc()
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

    # The quota is charged last, so a batch rejected above does not use it up.
    quota = await limiter.check_application(
        api_key.application_id, settings.default_monthly_quota, cost=len(request.urls)
    )
    if not quota["allowed"]:
        RATE_LIMIT_HITS_TOTAL.labels(layer=quota["layer"]).inc()
        raise HTTPException(
            status_code=429,
            detail="Application quota exceeded",
            headers={"Retry-After": str(math.ceil(quota["retry_after_s"]))},
        )

    # Enqueue one real fetch_task per URL via the shared JobService.
    redis_client = req.app.state.redis
    return await BatchService.create_batch(
        urls=[str(url) for url in request.urls],
        mode=request.mode,
        api_key=api_key,
        redis_client=redis_client,
        arq_pool=req.app.state.arq_pool,
        callback_url=str(request.callback_url) if request.callback_url else None,
        options=request.options,
    )


@router.get("/{batch_id}/status", response_model=BatchStatusResponse)
async def get_batch_status(
    batch_id: str,
    req: Request,
    api_key: ApiKey = Depends(require_scope(SCOPE_FETCH)),
):
    result = await BatchService.get_batch_status(
        batch_id, req.app.state.redis, api_key.application_id
    )
    if not result:
        raise HTTPException(status_code=404, detail="Batch not found")
    return result


@router.get("/{batch_id}/results")
async def get_batch_results(
    batch_id: str,
    req: Request,
    api_key: ApiKey = Depends(require_scope(SCOPE_FETCH)),
):
    results = await BatchService.get_batch_results(
        batch_id, req.app.state.redis, api_key.application_id
    )
    if not results:
        raise HTTPException(status_code=404, detail="Batch not found")
    return results
