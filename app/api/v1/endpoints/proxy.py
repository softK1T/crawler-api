import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from app.core.security import verify_api_key
from app.core.config import settings
from app.services.proxy_singleton import get_proxy_pool, reset_proxy_pool
from app.services.geo_proxy_pool import GeoProxyPool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/proxy", tags=["proxy"])


@router.get("/stats")
async def proxy_stats(_api_key: str = Depends(verify_api_key)):
    """
    Returns current proxy pool health statistics.
    """
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
async def reset_pool(_api_key: str = Depends(verify_api_key)):
    """Reset all proxy health stats (unblocks blocked/bad proxies)."""
    pool = get_proxy_pool()
    if pool is None:
        return {"message": "Proxy pool not configured"}
    pool.reset_all()
    return {"message": "Proxy pool reset successfully", "total_proxies": len(pool.proxies)}


def _do_sync() -> dict:
    """Internal sync logic — called from both endpoint and startup."""
    if not settings.webshare_api_key:
        raise HTTPException(
            status_code=400,
            detail="WEBSHARE_API_KEY is not set. Add it to your .env file."
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
    background_tasks: BackgroundTasks,
    _api_key: str = Depends(verify_api_key),
):
    """
    Trigger a manual Webshare proxy sync.
    Fetches fresh proxy list from Webshare API, writes proxies.txt with country codes,
    and reloads the GeoProxyPool singleton.
    Requires WEBSHARE_API_KEY to be set in .env
    """
    result = _do_sync()
    logger.info("Manual Webshare sync: %d proxies", result["synced"])
    return {"message": "Webshare sync complete", **result}
