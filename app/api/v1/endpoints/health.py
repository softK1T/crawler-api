"""Health-check endpoints — liveness, readiness."""

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz():
    """Liveness — always returns 200. No dependency checks."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request):
    """Readiness — checks DB, Redis, and S3 client availability."""
    checks = {"db": "unknown", "redis": "unknown", "s3": "unknown"}
    healthy = True

    # DB check.
    try:
        from app.core.db import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            from sqlalchemy import text

            await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "fail"
        healthy = False

    # Redis check.
    try:
        redis_client = getattr(request.app.state, "redis", None)
        if redis_client:
            await redis_client.ping()
            checks["redis"] = "ok"
        else:
            checks["redis"] = "fail"
            healthy = False
    except Exception:
        checks["redis"] = "fail"
        healthy = False

    # S3 check (client initialized, no network call).
    try:
        warc_storage = getattr(request.app.state, "warc_storage", None)
        if warc_storage and getattr(warc_storage, "_s3", None):
            checks["s3"] = "ok"
        else:
            checks["s3"] = "fail"
            healthy = False
    except Exception:
        checks["s3"] = "fail"
        healthy = False

    status_code = 200 if healthy else 503
    return {"status": "ready" if healthy else "not_ready", "checks": checks}, status_code
