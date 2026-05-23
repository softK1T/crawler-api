from fastapi import APIRouter, HTTPException
from app.schemas.requests import BatchCrawlRequest
from app.schemas.responses import BatchResponse, BatchStatusResponse
from app.services.batch_service import BatchService
from app.core.ssrf_guard import validate_url_against_ssrf
from app.core.config import settings

router = APIRouter(prefix="/batches", tags=["batches"])


@router.post("/", response_model=BatchResponse, status_code=202)
async def create_crawl_batch(request: BatchCrawlRequest):
    if settings.ssrf_enabled:
        for url in request.urls:
            validate_url_against_ssrf(str(url))
    return BatchService.create_batch(
        urls=[str(url) for url in request.urls],
        headers=request.headers,
        timeout=request.timeout,
        delay=request.delay,
        use_proxy=request.use_proxy,
    )


@router.get("/{batch_id}/status", response_model=BatchStatusResponse)
async def get_batch_status(batch_id: str):
    status = BatchService.get_batch_status(batch_id)
    if not status:
        raise HTTPException(status_code=404, detail="Batch not found")
    return status


@router.get("/{batch_id}/results")
async def get_batch_results(batch_id: str):
    results = BatchService.get_batch_results(batch_id)
    if not results:
        raise HTTPException(status_code=404, detail="Batch not found")
    return results
