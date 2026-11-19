# Configuration

[Documentation](README.md) · [Runbook](runbook.md)

## Compose versus Python settings

Host/.env variables are interpolated only where Compose references them; they
are not all forwarded automatically. The default stack needs no .env. Its entrypoint
loads the secret volume and constructs the local PostgreSQL DSN. A host DATABASE_URL
alone does not point that entrypoint at an external DB. Other settings need a
reviewed Compose environment change/alternative deployment.

| Canonical Compose variable | Default |
|---|---|
| API_PORT | 8000, host loopback; container remains 8000 |
| WORKER_MAX_JOBS | 2 |
| ENABLE_PREMIUM_PROXY_TIERS / PROXY_SYNC_ENABLED | false / false |
| WEBSHARE_API_KEY | Empty; supply privately only when needed |

Source of truth: app/core/config.py, app/core/proxy_check_config.py and call sites.
A defined legacy field is not proof it controls the current fetch path.

## Main settings

| Setting | Python default | Canonical override / note |
|---|---|---|
| DATABASE_URL | Required | Generated local postgres/crawler DSN |
| REDIS_URL | localhost:6379/2 | redis://redis:6379/2 |
| ARQ_REDIS_URL | Empty → REDIS_URL | redis://redis:6379/2 |
| ENABLE_WARC / ENABLE_CALLBACKS | false / false | Off |
| ENABLE_TRACING / ENABLE_METRICS | true / true | Tracing off, metrics on |
| REQUIRE_CAMOUFOX / SSRF_ENABLED | true / true | On |
| PROXY_SYNC_ENABLED | true | Compose defaults off |
| WORKER_MAX_JOBS / WORKER_JOB_TIMEOUT_S | 6 / 120 | Compose jobs=2; not global pacing |
| BROWSER_POOL_SIZE | 2 | Compose fixes 1 |
| CURL_EXECUTOR_MAX_WORKERS | 8 | Curl executor |
| MAX_BATCH_SIZE | 20 | Schema separately caps 100 URLs |
| JOB_RESULT_TTL_S / RESULT_TTL_SECS | 86400 / 86400 | Different job/storage settings |
| KEY_RATE_LIMIT_RPM | 60 | Key admission |
| DEFAULT_MONTHLY_QUOTA | 100000 | Implementation uses rolling window |
| KEY_ROTATION_OVERLAP_HOURS | 24 | Bounded by existing key expiry |
| CIRCUIT_BREAKER_THRESHOLD / TIMEOUT_S | 20 / 300 | Inspect manager expiry semantics |
| PROXY_STICKY_TTL_S | 1800 | Sticky retention |
| ALLOWED_ORIGINS_RAW | * | Restrict before public exposure |
| LOG_LEVEL / METRICS_PATH | INFO / /metrics | Diagnostics |

REQUEST_TIMEOUT_SECS, REQUEST_DELAY_SECS, MAX_RETRIES, API_HOST/API_PORT and
BATCH_TIMEOUT_SECS are not a universal request contract. Current retry transport
passes a 30-second attempt timeout and reads policy attempt ceilings. BROWSER_IDLE/
ACQUIRE settings also require call-site verification before tuning. Do not infer
working options from an unused request schema.

## Candidate checker: PROXY_CHECK_ prefix

| Suffix | Default |
|---|---|
| ENABLED | false in Python, true in canonical Compose |
| ENDPOINT | https://1.1.1.1/cdn-cgi/trace |
| DATABASE_PATH | None; optional licensed Country MMDB |
| TIMEOUT_S / CONCURRENCY / BATCH_SIZE | 8 / 4 / 20; maximum 15 / 8 / 100 |
| TTL_S / RETRY_S | 21600 / 600; minimum 60 each |

Settings must reach the worker. Large imports take multiple minute passes. loc is
approximate exit country, not provider type or guaranteed rotating-gateway routing.
The optional MMDB is not bundled; licensing/download/update are operator tasks.

## Optional services

WARC needs ENABLE_WARC, S3_ENDPOINT_URL/S3_BUCKET/S3_ACCESS_KEY/S3_SECRET_KEY/S3_REGION.
Defaults: bucket crawler-warc, region us-east-1; empty endpoint means normal AWS
resolution, not included MinIO. Canonical Compose starts no MinIO. Size/age defaults
are 1000000000 bytes/3600 seconds; force-rotate defaults false. Mount durable DLQ
storage for default /var/lib/crawler/warc-dlq before relying on archival recovery.

Callbacks need ENABLE_CALLBACKS and CALLBACK_HMAC_SECRET; timeout 10 seconds,
max retries 3. Review network/ownership policy before enabling. Tracing needs an
OTLP endpoint. Provider sync needs private credentials and explicit enablement;
its actual cron is in arq_worker.py, not implied by an interval field alone.

## Browsers and resources

Packages: Playwright 1.61.0, Camoufox 0.4.11. Browser: recovered Camoufox
159.0-beta.42 Linux x86_64, in this project's runtime release. SHA256:

```text
549445fd1a070901cfb7e3f1309e0613f6dd117180d1cd01edd2df61606f9804
```

PLAYWRIGHT_BROWSERS_PATH=/ms-playwright; Camoufox cache
/home/crawler/.cache/camoufox; Docker MAX_CONCURRENT_CAMOUFOX=1. Worker shared
memory is 1 GB, not a total RSS cap. Default canonical Compose has no mem_limit:
set resource/egress budgets before untrusted load. MOZ_DISABLE_CONTENT_SANDBOX=1
is a Firefox compatibility trade-off, not a hardened sandbox. Native ARM64 needs
its own verified artifact; a checksum is not an upstream signature.
