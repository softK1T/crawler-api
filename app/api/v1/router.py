from fastapi import APIRouter, Depends
from app.api.v1.endpoints import jobs, batches
from app.core.security import verify_api_key

api_router = APIRouter(
    prefix="/v1",
    dependencies=[Depends(verify_api_key)],
)

api_router.include_router(jobs.router)
api_router.include_router(batches.router)
