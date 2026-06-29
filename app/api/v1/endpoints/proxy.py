from fastapi import APIRouter, Depends
from app.core.security import verify_api_key
from app.services.proxy_singleton import get_proxy_pool
from app.services.geo_proxy_pool import GeoProxyPool

router = APIRouter(prefix="/proxy", tags=["proxy"])


@router.get("/stats")
async def proxy_stats(_api_key: str = Depends(verify_api_key)):
    """
    Returns current proxy pool health statistics.
    Note: stats reflect the API process pool, not Celery worker pool.
    Celery worker stats are per-process and not exposed via HTTP.
    """
    pool = get_proxy_pool()
    if pool is None:
        return {
            "enabled": False,
            "message": "Proxy pool not configured. Set PROXY_FILE env var.",
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
async def reset_proxy_pool(_api_key: str = Depends(verify_api_key)):
    """Reset all proxy health stats (unblocks blocked/bad proxies)."""
    pool = get_proxy_pool()
    if pool is None:
        return {"message": "Proxy pool not configured"}
    pool.reset_all()
    return {"message": "Proxy pool reset successfully", "total_proxies": len(pool.proxies)}
