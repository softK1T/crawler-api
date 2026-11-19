"""Liveness and dependency readiness, including short-lived worker capabilities."""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz():
    """Process liveness; does not imply dependencies or browsers are ready."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request):
    """Readiness requires DB, Redis, and a recent successful worker heartbeat."""
    checks = {
        "db": "fail",
        "redis": "fail",
        "worker": "fail",
        "playwright": "fail",
        "camoufox": "fail",
        "s3": "disabled" if not settings.enable_warc else "fail",
    }
    try:
        from sqlalchemy import text

        from app.core.db import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:
        logger.debug("Readiness DB check failed", exc_info=True)

    try:
        client = getattr(request.app.state, "redis", None)
        if client is not None:
            await client.ping()
            checks["redis"] = "ok"
            raw = await client.get("crawler:worker:capabilities")
            if raw:
                capabilities = json.loads(raw)
                checks["worker"] = "ok"
                checks["playwright"] = "ok" if capabilities.get("playwright") else "fail"
                checks["camoufox"] = "ok" if capabilities.get("camoufox") else "fail"
    except Exception:
        logger.debug("Readiness worker check failed", exc_info=True)

    if settings.enable_warc:
        storage = getattr(request.app.state, "warc_storage", None)
        if storage is not None and getattr(storage, "_s3", None):
            checks["s3"] = "ok"

    required = ["db", "redis", "worker", "playwright"]
    if settings.require_camoufox:
        required.append("camoufox")
    if settings.enable_warc:
        required.append("s3")
    healthy = all(checks[name] == "ok" for name in required)
    return JSONResponse(
        content={"status": "ready" if healthy else "not_ready", "checks": checks},
        status_code=200 if healthy else 503,
    )
