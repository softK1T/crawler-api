import logging
import uuid
from contextlib import asynccontextmanager

import redis as redis_sync
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.router import api_router
from app.core.config import settings

logger = logging.getLogger(__name__)

CORRELATION_ID_HEADER = "X-Correlation-ID"


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """
    Reads X-Correlation-ID from request headers (or generates one).
    Injects it into every response so clients can trace distributed calls.
    """
    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response


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

app.add_middleware(CorrelationIDMiddleware)
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
