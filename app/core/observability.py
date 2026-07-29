"""Prometheus metrics and OpenTelemetry tracing bootstrap.

Module-level singletons are safe because they are instantiated at import time
before any request is served.  The GIL protects registry registration.
"""

import logging

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)

# ── Prometheus metrics ───────────────────────────────────────────────────────

REQUEST_LATENCY_MS = Histogram(
    "crawler_request_latency_ms",
    "HTTP/API and worker fetch latency in ms",
    ["component", "endpoint", "method", "status_code"],
    buckets=(10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, float("inf")),
)

BLOCK_RATE_TOTAL = Counter(
    "crawler_block_rate_total",
    "Blocked fetches by domain and engine",
    ["domain", "engine", "reason"],
)

QUEUE_DEPTH = Gauge(
    "crawler_queue_depth",
    "Approximate async queue depth",
    ["queue_name"],
)

PROXY_HEALTH = Gauge(
    "crawler_proxy_health_score",
    "Current proxy health score",
    ["proxy_id", "pool_id", "country"],
)

WARC_BYTES_TOTAL = Counter(
    "crawler_warc_bytes_total",
    "Total archived WARC bytes written",
    ["warc_type"],
)

DEDUP_RATIO = Gauge(
    "crawler_dedup_ratio",
    "Deduplicated revisit ratio over total archived records",
)

PROXY_COST_EUR_TOTAL = Counter(
    "crawler_proxy_cost_eur_total",
    "Estimated proxy traffic cost in EUR",
    ["provider"],
)

RATE_LIMIT_HITS_TOTAL = Counter(
    "crawler_rate_limit_hits_total",
    "Rate limit denials by layer",
    ["layer"],
)

BROWSER_POOL_SIZE = Gauge(
    "crawler_browser_pool_size",
    "Browsers launched in pool",
)

BROWSER_POOL_IN_USE = Gauge(
    "crawler_browser_pool_in_use",
    "Browsers checked out",
)

WARC_DLQ_ENTRIES = Gauge(
    "crawler_warc_dlq_entries",
    "WARC files pending re-upload",
)

# ── DEDUP_RATIO tracking ─────────────────────────────────────────────────────
_total_records: int = 0
_revisit_records: int = 0


def _update_dedup_ratio() -> None:
    if _total_records > 0:
        DEDUP_RATIO.set(_revisit_records / _total_records)
    else:
        DEDUP_RATIO.set(0.0)


# ── Tracing bootstrap ────────────────────────────────────────────────────────


def setup_tracing(settings) -> None:
    """Initialize OpenTelemetry with OTLP or console exporter."""
    if not settings.enable_tracing:
        return
    from opentelemetry import trace
    from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {SERVICE_NAME: settings.service_name, SERVICE_VERSION: settings.service_version}
    )

    from opentelemetry.sdk.trace.export import SpanExporter

    exporter: SpanExporter
    if settings.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=settings.otlp_endpoint)
    else:
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        exporter = ConsoleSpanExporter()

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Instrument FastAPI and httpx.
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXInstrumentor

        FastAPIInstrumentor().instrument()
        HTTPXInstrumentor().instrument()
    except Exception:
        logger.warning("OTel instrumentation failed", exc_info=True)


def get_tracer(name: str = "crawler-api"):
    from opentelemetry import trace

    return trace.get_tracer(name)


# ── Metrics helpers ───────────────────────────────────────────────────────────


def get_metrics_response() -> None:
    """Return a Starlette Response with Prometheus metrics."""
    from fastapi.responses import Response

    from app.core.config import settings

    if not settings.enable_metrics:
        return Response(content="", media_type=CONTENT_TYPE_LATEST)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def observe_queue_depth(redis_client, queue_name: str = "arq:crawler") -> None:
    """Read arq queue depth from Redis and set QUEUE_DEPTH gauge."""
    try:
        depth = await redis_client.llen(f"arq:queue:{queue_name}")
        QUEUE_DEPTH.labels(queue_name=queue_name).set(depth or 0)
    except Exception:
        logger.debug("Failed to observe queue depth", exc_info=True)


def update_proxy_health_metric(proxy) -> None:
    """Set PROXY_HEALTH gauge from a Proxy ORM row."""
    PROXY_HEALTH.labels(
        proxy_id=str(proxy.id),
        pool_id=str(proxy.pool_id),
        country=proxy.country or "unknown",
    ).set(float(proxy.health_score))


def record_archive_metrics(*, bytes_written: int, is_revisit: bool) -> None:
    """Increment WARC bytes counter and update dedup ratio."""
    warc_type = "revisit" if is_revisit else "response"
    WARC_BYTES_TOTAL.labels(warc_type=warc_type).inc(bytes_written)

    global _total_records, _revisit_records
    _total_records += 1
    if is_revisit:
        _revisit_records += 1
    _update_dedup_ratio()
