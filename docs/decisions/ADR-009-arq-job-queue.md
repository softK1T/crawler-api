# ADR-009: arq Job Queue

## Status
Accepted

## Context
The platform needs an async job queue for crawl tasks. The existing Celery
implementation uses gevent workers, which conflict with asyncio.run() used by
browser-mode fetchers (audit finding HIGH). A replacement must support:
async task functions natively, clean startup/shutdown hooks, Redis-backed
state, and low operational overhead on the target i5-8600 server.

## Decision
Use **arq** (async rq) instead of Celery for new fetch jobs.

Rationale:
- arq task functions are native async def — no asyncio.run() in workers,
  eliminating the gevent+asyncio deadlock risk.
- arq startup/shutdown hooks allow ProxyManager and WarcStorage to be
  initialized once per worker process (not per task).
- arq has no scheduler daemon requirement (unlike Celery Beat); periodic
  proxy sync will move to a cron-style arq function in Stage 12.
- Operational cost: arq requires only Redis (already in the stack).
  No RabbitMQ, no Flower, no separate beat process.

## Job ID decoupling
arq assigns its own internal job IDs. We generate str(uuid4()) at the API
layer and pass it as a task argument. Job state is stored under our own Redis
keys (job:{job_id}:status, :result, :error), not in arq's result backend
(keep_result=0). This decouples job identity from arq internals and allows
future migration to a different queue without changing the API contract.

## Idempotency
Idempotency keys are stored as idempotency:{application_id}:{key} with 24h TTL
(job_result_ttl_s). The application_id prefix prevents cross-tenant key
collisions. A caller providing the same Idempotency-Key within 24h receives
the original job_id with HTTP 200 and header Idempotency-Key-Status: replayed.

## Sync polling
POST /v1/fetch with options.sync=true polls Redis for job completion with
asyncio.sleep(0.1) intervals up to 30s. This blocks one API worker slot per
sync request. Acceptable at the current scale target (single-server,
<50 concurrent sync requests). A WebSocket or SSE notification channel is
deferred to a future stage.

## HMAC callback signature
X-Crawler-Signature: sha256=<hex> — identical format to GitHub webhook
signatures. Chosen for developer familiarity; most HTTP testing tools and
client libraries already have built-in support for this format.

## Celery compatibility shim
submit_crawl() in job_service.py delegates to JobService.enqueue() and returns
a uuid4 job_id. Legacy Celery-based result polling will time out (returns
JobStatus.RUNNING indefinitely for Celery-originated jobs). This is intentional:
the shim prevents import errors and startup crashes; actual Celery job results
are not migrated. The shim will be removed in Stage 13 after full verification.

## Consequences
- Celery Beat (proxy sync schedule) is no longer triggered. The periodic
  Webshare sync must be moved to an arq cron function (Stage 12).
- arq workers must be added to docker-compose as a separate service (Stage 12).
- retry_jobs=False: all retry logic lives inside fetch_with_retry (Stage 6).
  arq-level retries would cause double-crawls on transient failures.
