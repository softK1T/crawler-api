"""Health-check endpoint — fully async (no blocking I/O)."""

import logging
from urllib.parse import urlparse

from fastapi import APIRouter

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    import asyncpg
    import redis.asyncio as aioredis

    status = {"api": "ok", "redis": "unknown", "postgres": "unknown"}

    # Redis check (async)
    try:
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        await r.ping()
        await r.aclose()
        status["redis"] = "ok"
    except Exception as e:
        status["redis"] = f"error: {e}"

    # PostgreSQL check (async)
    try:
        db_str = str(settings.database_url).replace("+asyncpg", "")
        parsed = urlparse(db_str)
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
