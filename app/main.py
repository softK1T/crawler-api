import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.middleware.correlation_id import CorrelationIdMiddleware

logger = logging.getLogger(__name__)


async def _startup_proxy_sync():
    """On startup: if WEBSHARE_API_KEY is set, sync proxies from Webshare API."""
    if not settings.webshare_api_key:
        logger.info("WEBSHARE_API_KEY not set — skipping auto proxy sync")
        return
    try:
        from app.services.webshare_sync import sync_webshare_to_file
        from app.services.proxy_singleton import reset_proxy_pool, get_proxy_pool
        logger.info("Auto-syncing proxies from Webshare...")
        count = sync_webshare_to_file(
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
    await _startup_proxy_sync()
    yield


app = FastAPI(
    title="Crawler API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CorrelationIdMiddleware)

app.include_router(api_router)


@app.get("/health")
async def health():
    import redis
    import asyncpg
    from urllib.parse import urlparse

    status = {"api": "ok", "redis": "unknown", "postgres": "unknown"}

    # Redis check
    try:
        r = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        status["redis"] = "ok"
    except Exception as e:
        status["redis"] = f"error: {e}"

    # PostgreSQL check
    try:
        parsed = urlparse(settings.database_url.replace("+asyncpg", ""))
        conn = await asyncpg.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=parsed.username,
            password=parsed.password,
            database=parsed.path.lstrip("/"),
            timeout=3,
        )
        await conn.close()
        status["postgres"] = "ok"
    except Exception as e:
        status["postgres"] = f"error: {e}"

    return status
