from fastapi import APIRouter
from app.api.v1.endpoints import jobs, batches

api_router = APIRouter(prefix="/v1")

api_router.include_router(jobs.router)
api_router.include_router(batches.router)
