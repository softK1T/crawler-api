from fastapi import APIRouter
from app.api.v1.endpoints import jobs, batches, projects

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(jobs.router)
api_router.include_router(batches.router)
api_router.include_router(projects.router)
