"""arq fetch task — orchestrates crawler, WARC archival, and callback delivery."""

import asyncio
import json
import logging
import math
import time
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


async def fetch_task(
    ctx: dict,
    *,
    job_id: str,
    url: str,
    mode: str,
    api_key_prefix: str,
    application_id: str,
    domain: str,
    proxy_pool_id: str | None,
    callback_url: str | None,
    options: dict,
    trace_id: str | None = None,
) -> None:
    """arq task: fetch a URL, archive, and deliver callback."""
    started = time.perf_counter()
    settings = ctx["settings"]
    redis_client = ctx["redis"]

    from app.core.logging_config import bind_context

    bind_context(trace_id=trace_id or job_id, job_id=job_id, application_id=application_id)

    async with ctx["db_factory"]() as db:
        try:
            # 1. Mark running.
            await _set_status(redis_client, job_id, "running", settings.job_result_ttl_s)

            # 2. Resolve policy.
            from app.services.policy_resolver import resolve_policy

            policy = await resolve_policy(url, db)

            # 3. Select fetcher — map API mode to engine name.
            from app.services.fetchers import get_fetcher

            _MODE_TO_ENGINE = {
                "static": "httpx",
                "stealth": "curl_cffi",
                "browser": "playwright",
                "camoufox": "playwright",
            }
            engine = (
                policy.engine if policy and policy.engine else _MODE_TO_ENGINE.get(mode, "httpx")
            )
            fetcher = get_fetcher(engine, browser_pool=ctx.get("browser_pool"))

            # 4. Execute fetch with retry.
            from app.services.fetchers.base import fetch_with_retry

            # Extract proxy overrides from request options (three-level resolution:
            # request > domain_policy > defaults).
            req_use_proxy = options["use_proxy"] if "use_proxy" in options else None
            req_proxy_country = options["proxy_country"] if "proxy_country" in options else None
            req_proxy_type = options.get("proxy_type")  # "residential" | "datacenter" | None

            result = await fetch_with_retry(
                fetcher=fetcher,
                url=url,
                policy=policy,
                proxy_manager=ctx.get("proxy_manager"),
                db=db,
                sticky_key=job_id,
                trace_id=job_id,
                use_proxy=req_use_proxy,
                proxy_country=req_proxy_country,
                proxy_type=req_proxy_type,
            )

            # 5. Block detection metric.
            if result.blocked:
                from app.core.observability import BLOCK_RATE_TOTAL

                BLOCK_RATE_TOTAL.labels(
                    domain=domain,
                    engine=result.engine,
                    reason=result.block_reason or "unknown",
                ).inc()

            # 6. Normalize API response body (server-side decompression, ADR-018).
            from app.services.content_decoder import decode_body, integrity, normalize_headers

            raw_for_decode = result.raw_body or result.body
            content_encoding = (result.raw_headers or result.headers).get("content-encoding")
            try:
                api_body, original_encoding = decode_body(raw_for_decode, content_encoding)
            except Exception:
                api_body = result.body
                original_encoding = content_encoding

            result.headers = normalize_headers(result.headers, len(api_body))
            result.body = api_body

            ing = integrity(api_body)
            ing["original_content_encoding"] = original_encoding

            # 7. Archive if not blocked (WARC gets raw transport bytes).
            warc_index = None
            warc_body = result.raw_body or result.body
            if not result.blocked and ctx.get("warc_storage"):
                warc_index = await ctx["warc_storage"].archive(
                    fetch_result=result,
                    request_log_id=None,
                    db=db,
                    warc_body=warc_body,
                )
                if warc_index is not None:
                    from app.core.observability import record_archive_metrics

                    record_archive_metrics(
                        bytes_written=len(warc_body),
                        is_revisit=getattr(warc_index, "is_revisit", False),
                    )

            # 8. Serialize and store result.
            from app.schemas.fetch import FetchResultSchema

            schema = FetchResultSchema.from_result(result, integrity_fields=ing)

            await _set_status(
                redis_client,
                job_id,
                "completed",
                settings.job_result_ttl_s,
                result_data=schema.model_dump(),
            )

            # 8. Usage counter upsert.
            await _upsert_usage(db, application_id, len(result.body))

            # 8b. Cost metric — only for proxied fetches.
            if result.proxy_id is not None:
                from app.core.observability import PROXY_COST_EUR_TOTAL

                PROXY_COST_EUR_TOTAL.labels(provider="webshare").inc(
                    _bytes_to_eur_cost(len(result.body))
                )

            # 9. Latency metric.
            _record_latency("completed", started)

            # 10. Callback.
            if callback_url and settings.callback_hmac_secret:
                _schedule_callback(
                    job_id=job_id,
                    status="completed",
                    callback_url=callback_url,
                    result=schema,
                    secret=settings.callback_hmac_secret,
                )

        except asyncio.CancelledError:
            await _set_error(redis_client, job_id, "Worker shutdown", settings.job_result_ttl_s)
            await _upsert_usage(db, application_id, 0)
            _record_latency("failed", started)
            if callback_url and settings.callback_hmac_secret:
                _schedule_callback(
                    job_id=job_id,
                    status="failed",
                    callback_url=callback_url,
                    error="Worker shutdown",
                    secret=settings.callback_hmac_secret,
                )
            raise

        except Exception as exc:
            logger.error("fetch_task failed job=%s: %s", job_id, exc)
            await _set_error(redis_client, job_id, str(exc), settings.job_result_ttl_s)
            await _upsert_usage(db, application_id, 0)
            _record_latency("failed", started)
            if callback_url and settings.callback_hmac_secret:
                _schedule_callback(
                    job_id=job_id,
                    status="failed",
                    callback_url=callback_url,
                    error=str(exc),
                    secret=settings.callback_hmac_secret,
                )


# ── helpers ──────────────────────────────────────────────────────────────────


async def _set_status(redis_client, job_id: str, status: str, ttl: int, result_data=None) -> None:
    now = datetime.now(UTC).isoformat()
    # Preserve created_at from the original status payload.
    raw = await redis_client.get(f"job:{job_id}:status")
    created_at = now
    if raw:
        try:
            existing = json.loads(raw)
            created_at = existing.get("created_at", now)
        except json.JSONDecodeError:
            pass
    payload = {"status": status, "updated_at": now, "created_at": created_at}
    if result_data is not None:
        payload["result"] = result_data
    await redis_client.set(f"job:{job_id}:status", json.dumps(payload), ex=ttl)


async def _set_error(redis_client, job_id: str, error: str, ttl: int) -> None:
    now = datetime.now(UTC).isoformat()
    raw = await redis_client.get(f"job:{job_id}:status")
    created_at = now
    if raw:
        try:
            existing = json.loads(raw)
            created_at = existing.get("created_at", now)
        except json.JSONDecodeError:
            pass
    payload = {"status": "failed", "updated_at": now, "created_at": created_at}
    await redis_client.set(f"job:{job_id}:status", json.dumps(payload), ex=ttl)
    await redis_client.set(f"job:{job_id}:error", error, ex=ttl)


def _schedule_callback(
    *,
    job_id: str,
    status: str,
    callback_url: str,
    result=None,
    error: str | None = None,
    secret: str,
) -> None:
    from app.schemas.job import CallbackPayload, JobStatus
    from app.services.callback import deliver_callback

    payload = CallbackPayload(
        job_id=job_id,
        status=JobStatus(status),
        result=result,
        error=error,
        timestamp=datetime.now(UTC),
    )
    _task = asyncio.create_task(  # noqa: RUF006
        deliver_callback(callback_url, payload, secret),
    )


# ── Usage counter + metrics helpers ───────────────────────────────────────────

_COST_EUR_PER_GB = 3.50  # €3.50/GB — single source of truth for cost calculation.


def _bytes_to_eur_cost(body_bytes: int) -> float:
    """Return EUR cost for the given bytes at the standard rate."""
    return (body_bytes / (1024**3)) * _COST_EUR_PER_GB


async def _upsert_usage(db, application_id: str, body_bytes: int) -> None:
    """Upsert usage_counter row for the current month."""
    try:
        from uuid import UUID

        from sqlalchemy import text

        app_id = UUID(application_id)
        month_start = datetime.now(UTC).date().replace(day=1)
        cost_cents = math.ceil(_bytes_to_eur_cost(body_bytes) * 100)

        await db.execute(
            text(
                """
                INSERT INTO usage_counters (
                    application_id, period_month, request_count,
                    bytes_received, cost_eur_cents, updated_at
                ) VALUES (
                    :aid, :pm, 1, :br, :cc, now()
                )
                ON CONFLICT (application_id, period_month) DO UPDATE SET
                    request_count = usage_counters.request_count + 1,
                    bytes_received = usage_counters.bytes_received + :br,
                    cost_eur_cents = usage_counters.cost_eur_cents + :cc,
                    updated_at = now()
            """
            ),
            {"aid": app_id, "pm": month_start, "br": body_bytes, "cc": cost_cents},
        )
        await db.commit()
    except Exception:
        logger.warning("Usage counter upsert failed for app=%s", application_id, exc_info=True)


def _record_latency(status: str, started: float) -> None:
    try:
        from app.core.observability import REQUEST_LATENCY_MS

        elapsed = int((time.perf_counter() - started) * 1000)
        REQUEST_LATENCY_MS.labels(
            component="worker", endpoint="fetch_task", method="ARQ", status_code=status
        ).observe(elapsed)
    except Exception:
        pass


# ── Worker lifecycle ─────────────────────────────────────────────────────────


async def _verify_chromium() -> None:
    """Check that the Playwright Chromium executable exists on disk.

    Called at worker startup before accepting jobs.  If the binary is
    missing the worker must fail fast — readiness stays negative and no
    browser-mode jobs are silently degraded to httpx.
    """
    from pathlib import Path

    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)

    if not executable.is_file():
        raise RuntimeError(
            "PLAYWRIGHT_CHROMIUM_MISSING: "
            f"expected executable at {executable}; "
            "rebuild the worker image with Playwright Chromium installed"
        )
    logger.info("Chromium verified at %s", executable)


async def startup(ctx: dict) -> None:
    """arq worker startup — initialize shared resources."""
    import redis.asyncio as aioredis

    from app.core.config import settings
    from app.core.db import AsyncSessionLocal
    from app.core.logging_config import configure_logging
    from app.services.proxy_manager import ProxyManager
    from app.services.warc.storage import create_warc_storage
    from app.worker.browser_pool import ChromiumMissingError, browser_pool, verify_chromium

    configure_logging(settings.log_level)
    ctx["settings"] = settings
    ctx["redis"] = aioredis.from_url(settings.redis_url, decode_responses=False)
    ctx["db_factory"] = AsyncSessionLocal
    ctx["proxy_manager"] = ProxyManager(
        db_session_factory=AsyncSessionLocal,
        redis_client=ctx["redis"],
    )
    ctx["warc_storage"] = await create_warc_storage(settings)

    # Verify Chromium can actually launch (not just exist on disk).
    # Fail startup if missing — browser-mode jobs must not silently degrade.
    try:
        version = await verify_chromium()
        await browser_pool.start()
        ctx["browser_pool"] = browser_pool
        ctx["browser_ready"] = True
        logger.info("chromium_selfcheck_passed version=%s", version)
    except ChromiumMissingError:
        ctx["browser_ready"] = False
        logger.error("browser_selfcheck_failed error=PLAYWRIGHT_CHROMIUM_MISSING")
        raise

    logger.info("arq worker startup complete")


async def shutdown(ctx: dict) -> None:
    """arq worker shutdown — drain browser pool, flush WARC, close Redis."""
    # Drain browser pool.
    if ctx.get("browser_pool"):
        try:
            await ctx["browser_pool"].stop()
        except Exception:
            logger.warning("Browser pool shutdown failed", exc_info=True)

    # Flush WARC.
    try:
        if ctx.get("warc_storage"):
            await ctx["warc_storage"].shutdown_flush()
    except Exception:
        logger.warning("WARC shutdown flush failed", exc_info=True)

    # Shut down curl executor.
    try:
        from app.services.fetchers.curl_fetcher import _shutdown_executor

        _shutdown_executor()
    except Exception:
        pass

    # Close Redis.
    try:
        if ctx.get("redis"):
            await ctx["redis"].aclose()
    except Exception:
        pass
    logger.info("arq worker shutdown complete")
