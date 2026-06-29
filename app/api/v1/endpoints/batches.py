from fastapi import APIRouter, HTTPException, Depends
from app.schemas.requests import BatchCrawlRequest
from app.schemas.responses import BatchResponse, BatchStatusResponse
from app.services.batch_service import BatchService
from app.core.ssrf_guard import validate_url_against_ssrf
from app.core.security import verify_api_key
from app.core.config import settings

router = APIRouter(prefix="/batches", tags=["batches"])


@router.post("/", response_model=BatchResponse, status_code=202)
async def create_crawl_batch(
    request: BatchCrawlRequest,
    _api_key: str = Depends(verify_api_key),
):
    if settings.ssrf_enabled:
        for url in request.urls:
            validate_url_against_ssrf(str(url))
    return BatchService.create_batch(
        urls=[str(url) for url in request.urls],
        headers=request.headers,
        timeout=request.timeout,
        delay=request.delay,
        use_proxy=request.use_proxy,
        project_id=request.project_id,
        extract=request.extract,
        mode=request.mode,
    )


@router.get("/{batch_id}/status", response_model=BatchStatusResponse)
async def get_batch_status(
    batch_id: str,
    _api_key: str = Depends(verify_api_key),
):
    result = BatchService.get_batch_status(batch_id)
    if not result:
        raise HTTPException(status_code=404, detail="Batch not found")
    return result


@router.get("/{batch_id}/results")
async def get_batch_results(
    batch_id: str,
    _api_key: str = Depends(verify_api_key),
):
    results = BatchService.get_batch_results(batch_id)
    if not results:
        raise HTTPException(status_code=404, detail="Batch not found")
    return results
