from fastapi import APIRouter
from app.api.v1.endpoints import jobs, batches, projects, proxy, auth

api_router = APIRouter()
api_router.include_router(jobs.router)
api_router.include_router(batches.router)
api_router.include_router(projects.router)
api_router.include_router(proxy.router)
api_router.include_router(auth.router)
