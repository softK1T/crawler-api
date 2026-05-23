import logging
from contextlib import asynccontextmanager

import redis as redis_sync
from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.security import verify_api_key

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Crawler API starting up")
    yield
    logger.info("Crawler API shutting down")


app = FastAPI(
    title="Crawler API",
    version="1.0.0",
    description="High-performance web crawling microservice",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(api_router)


@app.get("/", include_in_schema=False)
async def root():
    return {"message": "Crawler API v1.0.0"}


@app.get("/health", tags=["system"])
async def health_check():
    """
    Real dependency health check.
    Returns 200 if healthy, 503 if any dependency is degraded.
    """
    checks: dict = {}

    # Check Redis
    try:
        r = redis_sync.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        logger.error("Redis health check failed: %s", exc)
        checks["redis"] = "error"

    all_ok = all(v == "ok" for v in checks.values())
    status_str = "healthy" if all_ok else "degraded"
    http_status = 200 if all_ok else 503

    return JSONResponse(
        status_code=http_status,
        content={"status": status_str, "checks": checks},
    )
