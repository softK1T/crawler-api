# ADR-014: Cleanup and Performance (Stage 14)

## Status
Accepted — 2026-07-29

## Context
Stage 13 left several ADR commitments open: Celery compat shim, periodic proxy
sync (Celery Beat → arq cron), shared executor for curl_cffi, browser pooling,
and cost-metric unification.

## Decisions

### 1. Celery Removal
Celery and all its infrastructure were deleted:
- `app/worker/celery_app.py`, `tasks/{crawl,auth,sync}.py`
- `app/services/{crawler,stealth_crawler}.py` (legacy engines)
- The `submit_crawl()` shim in `job_service.py`

**Removed API routes:**
- `POST /v1/jobs/` (legacy Celery compat — use `POST /v1/fetch`)
- `GET /v1/jobs/{id}/status` (returned hardcoded PENDING)
- `GET /v1/jobs/{id}/result` (legacy Celery result)

The sole job-status contract is now `GET /v1/jobs/{job_id}` from Stage 8.

`SmartProxyPool` and `ProxyRateLimiter` were moved into `app/services/geo_proxy_pool.py`
because `proxy_singleton.py` (used by the startup Webshare sync in `main.py`)
still imports them.  This is **known debt** — `ProxyManager` (ADR-006) is the
modern proxy-selection path.  Removal target: when `_startup_proxy_sync()` in
`main.py` is retired in favour of the arq cron sync.

### 2. Webshare Proxy Sync (arq cron)
`sync_proxies` in `app/worker/tasks/proxy_sync.py` runs every 30 minutes via
arq cron.  It fetches the Webshare proxy list, upserts rows by `(pool_id, url)`,
and deactivates rows absent from the provider response.  Hard-deletes are never
used — FK constraints from `request_log` and health history must survive.

### 3. Shared CurlFetcher Executor
A module-level `ThreadPoolExecutor` (size: `curl_executor_max_workers`, default
8) replaces the per-call `ThreadPoolExecutor(max_workers=1)`.  The executor is
created lazily on first use, reused across calls, and shut down via `atexit` +
the worker `shutdown()` hook.  The gevent-conflict fix (running sync curl_cffi
calls inside a thread executor) is preserved.

### 4. Cost Metric Unification
`_bytes_to_eur_cost()` in `fetch_task.py` is the single source of truth for
byte-to-EUR conversion at €3.50/GB.  Both `PROXY_COST_EUR_TOTAL` (proxied
fetches only) and the usage counter upsert reference it.

### 5. Browser Pool — Deferred to Stage 15
A bounded browser pool with context-per-fetch isolation is deferred.  Until
then, each Playwright fetch creates a fresh browser and context.  The invariant
(ADR-014) is:

> **Any future browser pool reuses *browsers*, never contexts.** Sharing a
> context across two fetches leaks cookies and storage across tenants.

The current `PlaywrightFetcher` creates a fresh context per fetch and closes it
in a `finally` block.  Two tests guard this invariant:
- `test_fresh_context_per_fetch_cookie_isolation`
- `test_ssrf_interception_handler_fires_per_fetch`

Stage 15 should implement the pool when browser-mode request rate exceeds 1
req/s (the ADR-007 threshold).

### 6. Deferred / Rejected Alternatives
- **geo_proxy_pool.py full removal**: blocked by `proxy_singleton.py` dependency.
  Will be addressed when the startup proxy sync is retired.
- **ProxyRateLimiter removal**: it is a per-proxy cooldown mechanism, not a
  duplicate of the Lua rate limiter (which implements API-level key/app/domain/proxy
  sliding windows).  These operate at different layers.
