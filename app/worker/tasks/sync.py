import logging

from app.core.config import settings
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="sync_webshare_proxies")
def sync_webshare_proxies():
    """
    Periodic Celery Beat task: re-syncs proxy list from Webshare API.
    Runs every WEBSHARE_SYNC_INTERVAL_SECS seconds (default 6h).
    Reloads GeoProxyPool singleton in the worker process.
    """
    if not settings.webshare_api_key:
        logger.info("sync_webshare_proxies: WEBSHARE_API_KEY not set, skipping")
        return {"skipped": True, "reason": "WEBSHARE_API_KEY not configured"}

    try:
        from app.services.proxy_singleton import get_proxy_pool, reset_proxy_pool
        from app.services.webshare_sync import sync_webshare_to_file

        count = sync_webshare_to_file(
            api_key=settings.webshare_api_key,
            output_path=settings.webshare_proxy_file,
        )
        reset_proxy_pool()
        pool = get_proxy_pool()
        stats = pool.get_stats() if pool else {}
        logger.info("Webshare sync complete: %d proxies, stats: %s", count, stats)
        return {"synced": count, "pool_stats": stats}
    except Exception as exc:
        logger.error("Webshare sync failed: %s", exc)
        raise
