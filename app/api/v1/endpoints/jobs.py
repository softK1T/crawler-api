"""Fetch endpoint — arq-backed async/sync job submission with idempotency."""

import asyncio
import math
import time
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import SCOPE_FETCH, require_scope, resolve_api_key
from app.core.config import settings
from app.core.db import get_db
from app.core.logging_config import bind_context
from app.core.observability import RATE_LIMIT_HITS_TOTAL, REQUEST_LATENCY_MS
from app.models.api_key import ApiKey
from app.schemas.job import JobCreate, JobResponse, JobStatus

# Legacy Celery compat schemas removed in Stage 14.
from app.services.policy_resolver import normalize_domain

router = APIRouter(prefix="")


# ── New arq-backed fetch ─────────────────────────────────────────────────────


@router.post("/v1/fetch", response_model=JobResponse, status_code=202)
async def create_fetch(
    body: JobCreate,
    req: Request,
    api_key: ApiKey = Depends(require_scope(SCOPE_FETCH)),
    db: AsyncSession = Depends(get_db),
):
    """Submit a fetch job via arq. Supports Idempotency-Key and sync mode."""
    started = time.perf_counter()

    # Resolve trace_id from correlation ID or OTel span.
    trace_id = req.headers.get("X-Correlation-ID")
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span and span.get_span_context().is_valid:
            trace_id = format(span.get_span_context().trace_id, "032x")
    except Exception:
        pass

    bind_context(trace_id=trace_id, application_id=str(api_key.application_id))

    # 1. Domain normalization.
    from urllib.parse import urlparse

    import redis.asyncio as aioredis

    from app.services.job_service import JobService

    domain = normalize_domain(urlparse(body.url).hostname or body.url)

    # 2. Rate limit check.
    rate_limiter = req.app.state.rate_limiter
    result = await rate_limiter.check_all(
        api_key_prefix=api_key.prefix,
        application_id=api_key.application_id,
        domain=body.url,
        proxy_id=None,
        domain_rps=1.0,
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
        RATE_LIMIT_HITS_TOTAL.labels(layer=result["layer"]).inc()

    # 3. Idempotency.
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=False)
    job_svc = JobService(redis_client)

    if body.idempotency_key:
        existing_job_id = await job_svc.handle_idempotency(
            body.idempotency_key, api_key.application_id
        )
        if existing_job_id:
            cached = await job_svc.get_result(existing_job_id)
            response = JSONResponse(
                status_code=200,
                content={
                    "job_id": existing_job_id,
                    "status": cached.status.value,
                    "created_at": cached.created_at.isoformat(),
                    "idempotency_key": body.idempotency_key,
                },
            )
            response.headers["Idempotency-Key-Status"] = "replayed"
            return response

    # 4. Merge request-level proxy overrides into options so they reach the worker.
    merged_options = dict(body.options)
    if body.use_proxy is not None:
        merged_options["use_proxy"] = body.use_proxy
    if body.proxy_country is not None:
        merged_options["proxy_country"] = body.proxy_country.upper()

    # 5. Enqueue.
    job_id = str(uuid4())
    proxy_pool_id = None

    await job_svc.enqueue(
        job_id=job_id,
        url=body.url,
        mode=body.mode,
        api_key=api_key,
        domain=domain,
        proxy_pool_id=proxy_pool_id,
        callback_url=body.callback_url,
        options=merged_options,
        trace_id=trace_id,
    )

    if body.idempotency_key:
        await job_svc.store_idempotency(body.idempotency_key, job_id, api_key.application_id)

    # 6. Sync mode: poll up to 30s.
    if merged_options.get("sync") is True:
        for _ in range(300):
            status = await job_svc.get_status(job_id)
            if status in (JobStatus.COMPLETED, JobStatus.FAILED):
                job_result = await job_svc.get_result(job_id)
                return JSONResponse(
                    status_code=200,
                    content=job_result.model_dump(mode="json"),
                )
            await asyncio.sleep(0.1)
        # Timeout — return running.
        return JSONResponse(
            status_code=202,
            content=JobResponse(
                job_id=job_id,
                status=JobStatus.RUNNING,
                created_at=datetime.now(UTC),
                idempotency_key=body.idempotency_key,
            ).model_dump(mode="json"),
        )

    # 7. Async mode — 202 Accepted.
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    REQUEST_LATENCY_MS.labels(
        component="api", endpoint="/v1/fetch", method="POST", status_code="202"
    ).observe(elapsed_ms)

    return JSONResponse(
        status_code=202,
        content=JobResponse(
            job_id=job_id,
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            idempotency_key=body.idempotency_key,
        ).model_dump(mode="json"),
        headers={
            "X-RateLimit-Limit": str(result["limit"]),
            "X-RateLimit-Remaining": str(result["remaining"]),
            "X-RateLimit-Reset": str(result["reset_at_ms"] // 1000),
        },
    )


# ── Status / result polling ──────────────────────────────────────────────────


@router.get("/v1/jobs/{job_id}")
async def get_job(
    job_id: str,
    _api_key: ApiKey = Depends(resolve_api_key),
):
    """Get job status and result."""
    import redis.asyncio as aioredis

    from app.services.job_service import JobService

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=False)
    job_svc = JobService(redis_client)
    return await job_svc.get_result(job_id)
