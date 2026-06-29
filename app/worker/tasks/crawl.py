import base64
import gzip
import logging
from datetime import datetime, timezone
from time import perf_counter
from typing import Dict, Any, Optional

from app.core.config import settings
from app.services.storage import storage
from app.services.crawler import Crawler, html_to_markdown, extract_with_selectors
from app.services.events import publish_event
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="crawl_page", acks_late=True)
def crawl_page(
    self,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
    delay: float = 1.0,
    use_proxy: bool = True,
    batch_id: Optional[str] = None,
    project_id: Optional[str] = None,
    extract: Optional[Dict[str, str]] = None,
    mode: str = "static",
) -> Dict[str, Any]:
    started = perf_counter()
    started_iso = datetime.now(timezone.utc).isoformat()
    job_id = self.request.id

    proxy_file = settings.proxy_file if use_proxy else None

    try:
        if mode == "browser":
            # Browser mode: run async crawl synchronously via asyncio
            import asyncio
            from app.services.crawler import crawl_browser
            raw = asyncio.run(crawl_browser(url, timeout=timeout))
        else:
            crawler = Crawler(
                proxy_file=proxy_file,
                max_retries=settings.max_retries,
                timeout=float(timeout),
                delay=delay,
                headers=headers,
                use_http2=settings.use_http2,
            )
            raw = crawler.crawl_raw(url)

        elapsed_ms = int((perf_counter() - started) * 1000)

        if raw is None:
            error_result = {
                "job_id": job_id,
                "project_id": project_id,
                "batch_id": batch_id,
                "url": url,
                "status_code": None,
                "content_type": None,
                "response_time_ms": elapsed_ms,
                "headers_trunc": {},
                "body_encoding": None,
                "body": None,
                "markdown": None,
                "extracted": None,
                "error_type": "CrawlError",
                "error_message": "Failed to crawl URL after all retries",
                "created_at": started_iso,
            }
            storage.save_job_result(job_id, error_result)
            publish_event("crawl", "crawl.failed", {"job_id": job_id, "url": url, "project_id": project_id})
            raise RuntimeError("Crawling failed after all retries")

        body_bytes, status_code, content_type, response_headers = raw
        html_str = body_bytes.decode("utf-8", "replace")

        # Convert HTML to Markdown
        markdown = html_to_markdown(html_str)

        # Extract structured data if selectors provided
        extracted = extract_with_selectors(html_str, extract) if extract else None

        # Compress and encode raw body for storage
        body_compressed = gzip.compress(body_bytes)
        body_encoded = base64.b64encode(body_compressed).decode("utf-8")

        success_result = {
            "job_id": job_id,
            "project_id": project_id,
            "batch_id": batch_id,
            "url": url,
            "status_code": status_code,
            "content_type": content_type,
            "response_time_ms": elapsed_ms,
            "headers_trunc": response_headers,
            "body_encoding": "base64+gzip",
            "body": body_encoded,
            "markdown": markdown,
            "extracted": extracted,
            "error_type": None,
            "error_message": None,
            "created_at": started_iso,
        }
        storage.save_job_result(job_id, success_result)

        # Publish event for downstream services (diff, analytics, notifications)
        publish_event("crawl", "crawl.completed", {
            "job_id": job_id,
            "url": url,
            "project_id": project_id,
            "status_code": status_code,
            "response_time_ms": elapsed_ms,
        })

        logger.info(f"Successfully crawled {url} in {elapsed_ms}ms (HTTP {status_code})")
        return {
            "job_id": job_id,
            "url": url,
            "status_code": status_code,
            "response_time_ms": elapsed_ms,
        }

    except Exception as e:
        elapsed_ms = int((perf_counter() - started) * 1000)

        error_result = {
            "job_id": job_id,
            "project_id": project_id,
            "batch_id": batch_id,
            "url": url,
            "status_code": None,
            "content_type": None,
            "response_time_ms": elapsed_ms,
            "headers_trunc": {},
            "body_encoding": None,
            "body": None,
            "markdown": None,
            "extracted": None,
            "error_type": e.__class__.__name__,
            "error_message": str(e),
            "created_at": started_iso,
        }
        storage.save_job_result(job_id, error_result)
        publish_event("crawl", "crawl.failed", {"job_id": job_id, "url": url, "project_id": project_id, "error": str(e)})

        logger.error(f"Failed to crawl {url}: {e}")
        raise
