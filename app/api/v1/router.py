from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    archive,
    auth_keys,
    batches,
    fetches,
    jobs,
    projects,
    proxy,
    proxy_candidates,
    usage,
)
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(jobs.router)
api_router.include_router(fetches.router)
api_router.include_router(batches.router)
api_router.include_router(projects.router)
api_router.include_router(proxy.router)
api_router.include_router(proxy_candidates.router)
api_router.include_router(auth_keys.router)
if settings.enable_warc:
    api_router.include_router(archive.router)
api_router.include_router(usage.router)
api_router.include_router(admin.router)
