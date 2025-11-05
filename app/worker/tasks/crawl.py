import base64
import gzip
import logging
from time import perf_counter
from typing import Dict, Any, Optional

from celery import states
from app.core.config import settings
from app.services.storage import storage
from app.services.crawler import Crawler, DEFAULT_HEADERS
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="crawl_page", acks_late=True)
def crawl_page(self, url: str, headers: Optional[Dict[str, str]] = None,
               timeout: int = 15, batch_id: Optional[str] = None) -> Dict[str, Any]:
    started = perf_counter()
    job_id = self.request.id

    request_headers = dict(DEFAULT_HEADERS)
    if headers:
        request_headers.update(headers)

    try:
        crawler = Crawler(
            proxy_file=settings.proxy_file,
            max_retries=settings.max_retries,
            timeout=float(timeout),
            delay=settings.request_delay_secs,
            headers=request_headers,
            use_http2=settings.use_http2,
        )

        body_bytes = crawler.crawl_bytes(url)
        elapsed_ms = int((perf_counter() - started) * 1000)

        if body_bytes is None:
            error_result = {
                "job_id": job_id,
                "batch_id": batch_id,
                "url": url,
                "status_code": None,
                "content_type": None,
                "response_time_ms": elapsed_ms,
                "headers_trunc": {},
                "body_encoding": None,
                "body": None,
                "error_type": "CrawlError",
                "error_message": "Failed to crawl URL after all retries",
            }
            storage.save_job_result(job_id, error_result)
            raise RuntimeError("Crawling failed after all retries")

        body_compressed = gzip.compress(body_bytes)
        body_encoded = base64.b64encode(body_compressed).decode('utf-8')

        success_result = {
            "job_id": job_id,
            "batch_id": batch_id,
            "url": url,
            "status_code": 200,
            "content_type": "text/html",
            "response_time_ms": elapsed_ms,
            "headers_trunc": {k: v for k, v in request_headers.items()},
            "body_encoding": "base64+gzip",
            "body": body_encoded,
            "error_type": None,
            "error_message": None,
        }
        storage.save_job_result(job_id, success_result)

        logger.info(f"Successfully crawled {url} in {elapsed_ms}ms")
        return {
            "job_id": job_id,
            "url": url,
            "status_code": 200,
            "response_time_ms": elapsed_ms
        }

    except Exception as e:
        elapsed_ms = int((perf_counter() - started) * 1000)

        error_result = {
            "job_id": job_id,
            "batch_id": batch_id,
            "url": url,
            "status_code": None,
            "content_type": None,
            "response_time_ms": elapsed_ms,
            "headers_trunc": {},
            "body_encoding": None,
            "body": None,
            "error_type": e.__class__.__name__,
            "error_message": str(e),
        }
        storage.save_job_result(job_id, error_result)

        logger.error(f"Failed to crawl {url}: {e}")
        raise
