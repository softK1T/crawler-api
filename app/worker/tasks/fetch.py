import base64
import gzip
import logging
from time import perf_counter
from typing import Dict, Any

from celery import states
from celery.utils.log import get_task_logger
from app.core.config import settings
from app.services.crawler import Crawler, DEFAULT_HEADERS
from app.services.storage import save_result
from app.worker.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(bind=True, name="app.worker.tasks.fetch_page", acks_late=True)
def fetch_page(self, url: str, headers: Dict[str, str] | None = None, timeout_s: int = 10) -> Dict[str, Any] | None:
    started = perf_counter()
    hdrs = dict(DEFAULT_HEADERS)
    if headers:
        hdrs.update(headers)
    try:
        crawler = Crawler(
            proxy_file=settings.proxy_file,
            max_retries=settings.max_retries,
            timeout=float(timeout_s or settings.request_timeout_secs),
            delay=settings.request_delay_secs,
            headers=hdrs,
            use_http2=settings.use_http2,
        )

        body_bytes = crawler.crawl_bytes(url)
        elapsed = int((perf_counter() - started) * 1000)
        if body_bytes is None:
            error_payload = {
                "task_id": self.request.id,
                "url": url,
                "status_code": None,
                "content_type": None,
                "response_time_ms": elapsed,
                "headers_trunc": {},
                "body_encoding": None,
                "body": None,
                "error_type": "FetchFailed",
                "error_message": "All retries failed",
            }
            save_result(self.request.id, error_payload)
            raise RuntimeError("All retries failed")

        body_b64_gz = base64.b64encode(gzip.compress(body_bytes)).decode('utf-8')
        result = {
            "task_id": self.request.id,
            "url": url,
            "status_code": 200,
            "content_type": None,
            "response_time_ms": elapsed,
            "headers_trunc": {k: v for k, v in crawler.headers.items()},
            "body_encoding": "base64+gzip",
            "body": body_b64_gz,
            "error_type": None,
            "error_message": None,
        }
        save_result(self.request.id, result)
        return {"stored": True, "task_id": self.request.id, "url": url, "status_code": 200}
    except Exception as e:
        elapsed_ms = int((perf_counter() - started) * 1000)
        error_payload = {
            "task_id": self.request.id,
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
        save_result(self.request.id, error_payload)
        raise