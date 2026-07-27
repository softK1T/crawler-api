import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.dependencies import SCOPE_ADMIN, require_scope, resolve_api_key
from app.core.config import settings
from app.models.api_key import ApiKey
from app.services.geo_proxy_pool import GeoProxyPool
from app.services.proxy_singleton import get_proxy_pool, reset_proxy_pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/proxy", tags=["proxy"])


@router.get("/stats")
async def proxy_stats(api_key: ApiKey = Depends(resolve_api_key)):
    """Returns current proxy pool health statistics. Read-only — any
    authenticated key can access."""
    pool = get_proxy_pool()
    if pool is None:
        return {
            "enabled": False,
            "message": "Proxy pool not configured. Set WEBSHARE_API_KEY or PROXY_FILE.",
        }

    base = pool.get_stats()
    geo = pool.get_geo_stats() if isinstance(pool, GeoProxyPool) else {}

    return {
        "enabled": True,
        "total_proxies": base["total_proxies"],
        "healthy": base["healthy"],
        "blocked": base["blocked"],
        "bad": base["bad"],
        "total_requests": base["total_requests"],
        "geo_breakdown": geo,
    }


@router.post("/reset")
async def reset_pool(_api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN))):
    """Reset all proxy health stats (unblocks blocked/bad proxies).
    Requires admin scope."""
    pool = get_proxy_pool()
    if pool is None:
        return {"message": "Proxy pool not configured"}
    pool.reset_all()
    return {"message": "Proxy pool reset successfully", "total_proxies": len(pool.proxies)}


def _do_sync() -> dict:
    """Internal sync logic — called from both endpoint and startup."""
    if not settings.webshare_api_key:
        raise HTTPException(
            status_code=400, detail="WEBSHARE_API_KEY is not set. Add it to your .env file."
        )
    from app.services.webshare_sync import sync_webshare_to_file

    count = sync_webshare_to_file(
        api_key=settings.webshare_api_key,
        output_path=settings.webshare_proxy_file,
    )
    reset_proxy_pool()
    pool = get_proxy_pool()  # reinitialise immediately
    return {
        "synced": count,
        "file": settings.webshare_proxy_file,
        "pool_healthy": pool.get_stats()["healthy"] if pool else 0,
    }


@router.post("/sync")
async def sync_webshare(
    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
):
    """Trigger a manual Webshare proxy sync. Requires admin scope."""
    result = _do_sync()
    logger.info("Manual Webshare sync: %d proxies", result["synced"])
    return {"message": "Webshare sync complete", **result}
