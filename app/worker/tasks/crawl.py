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
from app.services.proxy_singleton import get_proxy_pool
from app.services.geo_proxy_pool import detect_country_from_url
from app.services.stealth_crawler import StealthCrawler
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="crawl_page", acks_late=True)
def crawl_page(
    self,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    delay: float = 2.0,
    use_proxy: bool = True,
    batch_id: Optional[str] = None,
    project_id: Optional[str] = None,
    extract: Optional[Dict[str, str]] = None,
    mode: str = "static",
    proxy_country: Optional[str] = None,
    wait_for: Optional[str] = None,
    session_key: Optional[str] = None,
) -> Dict[str, Any]:
    started = perf_counter()
    started_iso = datetime.now(timezone.utc).isoformat()
    job_id = self.request.id

    resolved_country = proxy_country or detect_country_from_url(url)
    pool = get_proxy_pool() if use_proxy else None

    try:
        raw = None

        if mode == "static":
            crawler = Crawler(
                proxy_pool=pool,
                proxy_file=settings.effective_proxy_file if (use_proxy and pool is None) else None,
                max_retries=settings.max_retries,
                timeout=float(timeout),
                delay=delay,
                headers=headers,
                use_http2=settings.use_http2,
                proxy_country=resolved_country,
            )
            raw = crawler.crawl_raw(url)

        elif mode == "stealth":
            crawler = StealthCrawler(
                proxy_pool=pool,
                max_retries=settings.max_retries,
                timeout=float(timeout),
                delay=delay,
                headers=headers,
                proxy_country=resolved_country,
            )
            raw = crawler.crawl_raw(url)

        elif mode == "browser":
            import asyncio
            from app.services.stealth_crawler import crawl_playwright_stealth
            proxy_url = _pick_proxy_url(pool, resolved_country)
            raw = asyncio.run(crawl_playwright_stealth(
                url=url,
                timeout=timeout,
                proxy_url=proxy_url,
                wait_for=wait_for,
            ))

        elif mode == "camoufox":
            import asyncio
            from app.services.stealth_crawler import crawl_camoufox
            proxy_url = _pick_proxy_url(pool, resolved_country)
            raw = asyncio.run(crawl_camoufox(
                url=url,
                timeout=timeout,
                proxy_url=proxy_url,
                wait_for=wait_for,
                session_key=session_key,
            ))

        elapsed_ms = int((perf_counter() - started) * 1000)

        if raw is None:
            error_result = _make_error(job_id, project_id, batch_id, url, elapsed_ms, started_iso, mode)
            storage.save_job_result(job_id, error_result)
            publish_event("crawl", "crawl.failed", {
                "job_id": job_id, "url": url, "project_id": project_id,
                "proxy_country": resolved_country, "mode": mode,
            })
            raise RuntimeError(f"Crawling failed after all retries (mode={mode})")

        body_bytes, status_code, content_type, response_headers = raw
        html_str = body_bytes.decode("utf-8", "replace")
        markdown = html_to_markdown(html_str)
        extracted = extract_with_selectors(html_str, extract) if extract else None
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
        publish_event("crawl", "crawl.completed", {
            "job_id": job_id, "url": url, "project_id": project_id,
            "status_code": status_code, "response_time_ms": elapsed_ms,
            "proxy_country": resolved_country, "mode": mode,
        })
        logger.info("Crawled %s in %dms (HTTP %d) mode=%s country=%s", url, elapsed_ms, status_code, mode, resolved_country)
        return {"job_id": job_id, "url": url, "status_code": status_code, "response_time_ms": elapsed_ms, "mode": mode}

    except Exception as e:
        elapsed_ms = int((perf_counter() - started) * 1000)
        error_result = {
            "job_id": job_id, "project_id": project_id, "batch_id": batch_id,
            "url": url, "status_code": None, "content_type": None,
            "response_time_ms": elapsed_ms, "headers_trunc": {},
            "body_encoding": None, "body": None, "markdown": None,
            "extracted": None,
            "error_type": e.__class__.__name__, "error_message": str(e),
            "created_at": started_iso,
        }
        storage.save_job_result(job_id, error_result)
        publish_event("crawl", "crawl.failed", {
            "job_id": job_id, "url": url, "project_id": project_id,
            "error": str(e), "proxy_country": resolved_country, "mode": mode,
        })
        logger.error("Failed to crawl %s (mode=%s): %s", url, mode, e)
        raise


def _pick_proxy_url(pool, resolved_country: Optional[str]) -> Optional[str]:
    if not pool:
        return None
    from app.services.geo_proxy_pool import GeoProxyPool
    from app.services.crawler import auth_line_to_proxy_url
    if isinstance(pool, GeoProxyPool) and resolved_country:
        line = pool.pick_proxy_for_country(resolved_country)
    else:
        line = pool.pick_proxy_line()
    return auth_line_to_proxy_url(line) if line else None


def _make_error(job_id, project_id, batch_id, url, elapsed_ms, started_iso, mode):
    return {
        "job_id": job_id, "project_id": project_id, "batch_id": batch_id,
        "url": url, "status_code": None, "content_type": None,
        "response_time_ms": elapsed_ms, "headers_trunc": {},
        "body_encoding": None, "body": None, "markdown": None, "extracted": None,
        "error_type": "CrawlError",
        "error_message": f"Failed to crawl URL after all retries (mode={mode})",
        "created_at": started_iso,
    }
