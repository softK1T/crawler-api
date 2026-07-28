import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.endpoints.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import CrawlerAPIError
from app.core.logging_config import configure_logging
from app.core.observability import setup_tracing
from app.middleware.correlation_id import CorrelationIdMiddleware

# Configured at import time so startup logs are already structured.
configure_logging(settings.log_level)

logger = logging.getLogger(__name__)


async def _startup_proxy_sync():
    """On startup: if WEBSHARE_API_KEY is set, sync proxies from Webshare API."""
    if not settings.webshare_api_key:
        logger.info("WEBSHARE_API_KEY not set — skipping auto proxy sync")
        return
    try:
        from app.services.proxy_singleton import get_proxy_pool, reset_proxy_pool
        from app.services.webshare_sync import sync_webshare_to_file

        logger.info("Auto-syncing proxies from Webshare...")
        _count = sync_webshare_to_file(
            api_key=settings.webshare_api_key,
            output_path=settings.webshare_proxy_file,
        )
        reset_proxy_pool()
        pool = get_proxy_pool()
        if pool:
            stats = pool.get_stats()
            logger.info(
                "Proxy pool ready: %d total, %d healthy (geo: %s)",
                stats["total_proxies"],
                stats["healthy"],
                list(pool.get_geo_stats().keys()),
            )
        else:
            logger.warning("Proxy pool failed to initialise after sync")
    except Exception as exc:
        logger.error("Startup proxy sync failed: %s", exc)
        # Non-fatal — app continues without proxies


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_tracing(settings)
    await _startup_proxy_sync()

    # Initialize services in background — don't block server startup.
    _init_task = asyncio.create_task(_init_services(app))  # noqa: RUF006 — kept for cancellation

    yield

    # Graceful shutdown.
    try:
        warc_storage = getattr(app.state, "warc_storage", None)
        if warc_storage:
            await warc_storage.shutdown_flush()
    except Exception:  # noqa: S110
        pass


async def _init_services(app: FastAPI) -> None:
    """Initialize Redis, proxy manager, WARC storage in background."""
    try:
        import redis.asyncio as aioredis

        from app.core.db import AsyncSessionLocal
        from app.services.proxy_manager import ProxyManager
        from app.services.rate_limiter import RateLimiter

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=False)
        app.state.rate_limiter = RateLimiter(redis_client)
        app.state.redis = redis_client
        app.state.proxy_manager = ProxyManager(
            db_session_factory=AsyncSessionLocal,
            redis_client=redis_client,
        )

        from app.services.warc.storage import create_warc_storage

        warc_storage = await create_warc_storage(settings)
        app.state.warc_storage = warc_storage
        app.state.s3_client = warc_storage._s3

        from app.services.archive_reader import ArchiveReader

        app.state.archive_reader = ArchiveReader(app.state.s3_client, settings.s3_bucket)
        logger.info("All services initialized")
    except Exception:
        logger.warning("Service initialization incomplete", exc_info=True)


app = FastAPI(
    title="Crawler API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CorrelationIdMiddleware)

app.include_router(health_router)
app.include_router(api_router)


# ── Prometheus metrics endpoint ──────────────────────────────────────────────
from app.core.observability import get_metrics_response  # noqa: E402


@app.get(settings.metrics_path)
async def metrics():
    return get_metrics_response()


@app.exception_handler(CrawlerAPIError)
async def _crawler_api_error_handler(request, exc: CrawlerAPIError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error_code, "detail": exc.detail},
    )
