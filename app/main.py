import logging
import uuid
from contextlib import asynccontextmanager

import redis as redis_sync
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.db import create_tables

logger = logging.getLogger(__name__)

CORRELATION_ID_HEADER = "X-Correlation-ID"


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Crawler API starting up")
    await create_tables()
    logger.info("Database tables ensured")
    yield
    logger.info("Crawler API shutting down")


app = FastAPI(
    title="CrawlKit API",
    version="2.0.0",
    description="Self-hosted web crawling platform with multi-tenant support, persistent storage, and event streaming",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(CorrelationIDMiddleware)
app.include_router(api_router)


@app.get("/", include_in_schema=False)
async def root():
    return {"message": "CrawlKit API v2.0.0", "docs": "/docs"}


@app.get("/health", tags=["system"])
async def health_check():
    checks: dict = {}

    try:
        r = redis_sync.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        logger.error("Redis health check failed: %s", exc)
        checks["redis"] = "error"

    try:
        from app.core.db import engine
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        logger.error("PostgreSQL health check failed: %s", exc)
        checks["postgres"] = "error"

    all_ok = all(v == "ok" for v in checks.values())
    status_str = "healthy" if all_ok else "degraded"
    http_status = 200 if all_ok else 503

    return JSONResponse(
        status_code=http_status,
        content={"status": status_str, "checks": checks},
    )
