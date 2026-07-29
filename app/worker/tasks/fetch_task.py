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
            fetcher = get_fetcher(engine)

            # 4. Execute fetch with retry.
            from app.services.fetchers.base import fetch_with_retry

            result = await fetch_with_retry(
                fetcher=fetcher,
                url=url,
                policy=policy,
                proxy_manager=ctx.get("proxy_manager"),
                db=db,
                sticky_key=job_id,
                trace_id=job_id,
            )

            # 5. Block detection metric.
            if result.blocked:
                from app.core.observability import BLOCK_RATE_TOTAL

                BLOCK_RATE_TOTAL.labels(
                    domain=domain,
                    engine=result.engine,
                    reason=result.block_reason or "unknown",
                ).inc()

            # 6. Archive if not blocked.
            warc_index = None
            if not result.blocked and ctx.get("warc_storage"):
                warc_index = await ctx["warc_storage"].archive(
                    fetch_result=result, request_log_id=None, db=db
                )
                if warc_index is not None:
                    from app.core.observability import record_archive_metrics

                    record_archive_metrics(
                        bytes_written=len(result.body),
                        is_revisit=getattr(warc_index, "is_revisit", False),
                    )

            # 7. Serialize and store result.
            from app.schemas.fetch import FetchResultSchema

            schema = FetchResultSchema.from_result(result)

            await _set_status(
                redis_client,
                job_id,
                "completed",
                settings.job_result_ttl_s,
                result_data=schema.model_dump(),
            )

            # 8. Usage counter upsert.
            await _upsert_usage(db, application_id, len(result.body))

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


async def _upsert_usage(db, application_id: str, body_bytes: int) -> None:
    """Upsert usage_counter row for the current month."""
    try:
        from uuid import UUID

        from sqlalchemy import text

        app_id = UUID(application_id)
        month_start = datetime.now(UTC).date().replace(day=1)
        cost_cents = math.ceil((body_bytes / (1024**3)) * 350)  # €3.50/GB

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


async def startup(ctx: dict) -> None:
    """arq worker startup — initialize shared resources."""
    import redis.asyncio as aioredis

    from app.core.config import settings
    from app.core.db import AsyncSessionLocal
    from app.core.logging_config import configure_logging
    from app.services.proxy_manager import ProxyManager
    from app.services.warc.storage import create_warc_storage

    configure_logging(settings.log_level)
    ctx["settings"] = settings
    # Status keys must be on the same Redis DB the API reads from.
    ctx["redis"] = aioredis.from_url(settings.redis_url, decode_responses=False)
    ctx["db_factory"] = AsyncSessionLocal
    ctx["proxy_manager"] = ProxyManager(
        db_session_factory=AsyncSessionLocal,
        redis_client=ctx["redis"],
    )
    ctx["warc_storage"] = await create_warc_storage(settings)
    logger.info("arq worker startup complete")


async def shutdown(ctx: dict) -> None:
    """arq worker shutdown — flush WARC and close Redis."""
    try:
        if ctx.get("warc_storage"):
            await ctx["warc_storage"].shutdown_flush()
    except Exception:
        logger.warning("WARC shutdown flush failed", exc_info=True)
    try:
        if ctx.get("redis"):
            await ctx["redis"].aclose()
    except Exception:
        pass
    logger.info("arq worker shutdown complete")
