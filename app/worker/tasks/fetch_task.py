"""arq fetch task — orchestrates crawler, WARC archival, and callback delivery."""

import asyncio
import json
import logging
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
) -> None:
    """arq task: fetch a URL, archive, and deliver callback."""
    settings = ctx["settings"]
    redis_client = ctx["redis"]

    async with ctx["db_factory"]() as db:
        try:
            # 1. Mark running.
            await _set_status(redis_client, job_id, "running", settings.job_result_ttl_s)

            # 2. Resolve policy.
            from app.services.policy_resolver import resolve_policy

            policy = await resolve_policy(url, db)

            # 3. Select fetcher.
            from app.services.fetchers import get_fetcher

            engine = policy.engine if policy else mode
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

            # 5. Archive if not blocked.
            if not result.blocked and ctx.get("warc_storage"):
                await ctx["warc_storage"].archive(fetch_result=result, request_log_id=None, db=db)

            # 6. Serialize and store result.
            from app.schemas.fetch import FetchResultSchema

            schema = FetchResultSchema.from_result(result)

            await _set_status(
                redis_client,
                job_id,
                "completed",
                settings.job_result_ttl_s,
                result_data=schema.model_dump(),
            )

            # 7. Callback.
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
    payload = {"status": status, "updated_at": now}
    if result_data is not None:
        payload["result"] = result_data
    await redis_client.set(f"job:{job_id}:status", json.dumps(payload), ex=ttl)


async def _set_error(redis_client, job_id: str, error: str, ttl: int) -> None:
    now = datetime.now(UTC).isoformat()
    payload = {"status": "failed", "updated_at": now}
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


# ── Worker lifecycle ─────────────────────────────────────────────────────────


async def startup(ctx: dict) -> None:
    """arq worker startup — initialize shared resources."""
    import redis.asyncio as aioredis

    from app.core.config import settings
    from app.core.db import AsyncSessionLocal
    from app.services.proxy_manager import ProxyManager
    from app.services.warc.storage import create_warc_storage

    ctx["settings"] = settings
    redis_url = settings.arq_redis_url or settings.redis_url
    ctx["redis"] = aioredis.from_url(redis_url, decode_responses=False)
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
