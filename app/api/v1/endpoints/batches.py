from fastapi import APIRouter, HTTPException
from app.schemas.requests import BatchCrawlRequest
from app.schemas.responses import BatchResponse, BatchStatusResponse
from app.services.batch_service import BatchService

router = APIRouter(prefix="/batches", tags=["batches"])


@router.post("/", response_model=BatchResponse, status_code=202)
def create_crawl_batch(request: BatchCrawlRequest):
    return BatchService.create_batch(
        urls=[str(url) for url in request.urls],
        headers=request.headers,
        timeout=request.timeout
    )


@router.get("/{batch_id}/status", response_model=BatchStatusResponse)
def get_batch_status(batch_id: str):
    status = BatchService.get_batch_status(batch_id)
    if not status:
        raise HTTPException(status_code=404, detail="Batch not found")
    return status


@router.get("/{batch_id}/results")
def get_batch_results(batch_id: str):
    results = BatchService.get_batch_results(batch_id)
    if not results:
        raise HTTPException(status_code=404, detail="Batch not found")
    return results
