"""arq cron task — provider proxy sync orchestration."""

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


async def sync_proxies(ctx: dict) -> None:
    """Fetch provider proxy lists and reconcile DB rows."""
    if not settings.webshare_api_key:
        logger.info("sync_proxies: WEBSHARE_API_KEY not set — skipping")
        return

    from app.services.proxy_providers.webshare import WebshareProvider
    from app.services.proxy_sync_service import ProxySyncService

    service = ProxySyncService(
        db_factory=ctx["db_factory"],
        providers=[WebshareProvider(settings.webshare_api_key)],
    )
    result = await service.sync()
    logger.info("sync_proxies: done providers=%s", result.providers)
