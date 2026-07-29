from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    archive,
    auth,
    auth_keys,
    batches,
    jobs,
    projects,
    proxy,
    usage,
)

api_router = APIRouter()
api_router.include_router(jobs.router)
api_router.include_router(batches.router)
api_router.include_router(projects.router)
api_router.include_router(proxy.router)
api_router.include_router(auth.router)
api_router.include_router(auth_keys.router)
api_router.include_router(archive.router)
api_router.include_router(usage.router)
api_router.include_router(admin.router)
