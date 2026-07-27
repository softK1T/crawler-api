# ADR-011: Observability and Usage Metering

**Status:** Accepted | **Date:** 2026-07-27 | **Stage:** 10

## Metrics
8 Prometheus metrics: REQUEST_LATENCY_MS (histogram), BLOCK_RATE_TOTAL (counter),
QUEUE_DEPTH (gauge), PROXY_HEALTH (gauge), WARC_BYTES_TOTAL (counter),
DEDUP_RATIO (gauge), PROXY_COST_EUR_TOTAL (counter), RATE_LIMIT_HITS_TOTAL (counter).
Primary SLI: crawler_block_rate_total — blocked fetch ratio should stay <10%.

## Tracing
OTLP exporter when otlp_endpoint is set; ConsoleSpanExporter (stdout) when empty.
FastAPI and httpx auto-instrumentation at startup.

## Liveness vs Readiness
/healthz always 200. /readyz checks DB (SELECT 1), Redis (PING), S3 client.
Returns 503 with per-check status on failure.

## Usage Metering
UsageCounter upsert in arq worker only. Cost model: EUR 3.50/GB (fixed approx).
Failed fetches count request_count=1, bytes=0, cost=0.

## Proxy Cost
PROXY_COST_EUR_TOTAL uses fixed EUR 3.50/GB — approximation, not billing truth.
Real cost tracking requires provider API integration.
