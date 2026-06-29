import logging
from typing import Optional

from app.core.config import settings
from app.services.geo_proxy_pool import GeoProxyPool

logger = logging.getLogger(__name__)

_pool: Optional[GeoProxyPool] = None


def get_proxy_pool() -> Optional[GeoProxyPool]:
    """
    Return the global GeoProxyPool singleton.
    Initialised lazily on first call.
    Safe to call from both FastAPI (main process) and Celery workers
    because each process maintains its own singleton.
    Stats accumulate per-worker-process which is correct behaviour
    for gevent-based Celery (one OS process, many green threads).
    """
    global _pool
    if _pool is not None:
        return _pool

    if not settings.proxy_file:
        logger.info("PROXY_FILE not set — proxy pool disabled")
        return None

    try:
        with open(settings.proxy_file, "r") as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    except FileNotFoundError:
        logger.error("Proxy file not found: %s", settings.proxy_file)
        return None

    if not lines:
        logger.warning("Proxy file is empty: %s", settings.proxy_file)
        return None

    _pool = GeoProxyPool(
        proxy_list=lines,
        per_proxy_delay=settings.request_delay_secs,
    )
    return _pool


def reset_proxy_pool() -> None:
    """Force re-initialisation on next call (useful for testing / hot reload)."""
    global _pool
    _pool = None
