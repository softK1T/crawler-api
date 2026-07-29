import logging

from app.core.config import settings
from app.services.geo_proxy_pool import GeoProxyPool

logger = logging.getLogger(__name__)

_pool: GeoProxyPool | None = None


def get_proxy_pool() -> GeoProxyPool | None:
    """
    Return the global GeoProxyPool singleton.
    Initialised lazily on first call.
    Uses effective_proxy_file which prefers Webshare-synced file if API key is set.
    """
    global _pool
    if _pool is not None:
        return _pool

    proxy_file = settings.effective_proxy_file
    if not proxy_file:
        logger.info("No proxy file configured — proxy pool disabled")
        return None

    try:
        with open(proxy_file) as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    except FileNotFoundError:
        logger.error("Proxy file not found: %s", proxy_file)
        return None

    if not lines:
        logger.warning("Proxy file is empty: %s", proxy_file)
        return None

    _pool = GeoProxyPool(
        proxy_list=lines,
        per_proxy_delay=settings.request_delay_secs,
    )
    return _pool


def reset_proxy_pool() -> None:
    """Force re-initialisation on next call (useful after Webshare sync or testing)."""
    global _pool
    _pool = None
    logger.info("Proxy pool reset — will reload on next request")
