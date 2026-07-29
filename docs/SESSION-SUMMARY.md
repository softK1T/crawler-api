# Session Summary — crawler-api platform rebuild

## Stages (15 stages, 2026-07)

| Stage | Branch | Outcome |
|---|---|---|
| 0 | stage-0-audit | Architecture audit, requirement inventory |
| 1 | stage-1-security | Argon2id API keys, SSRF guard, URL policy |
| 2 | stage-1-security | ORM models, Alembic, partitioned request_log |
| 3 | stage-1-security | DB-backed auth, scopes, key lifecycle |
| 4 | stage-1-security | Policy resolver, 4-layer Lua rate limiter |
| 5 | stage-1-security | ProxyManager: weighted selection, circuit breaker |
| 6 | stage-1-security | FetcherProtocol: httpx, curl_cffi, playwright |
| 7 | stage-1-security | WARC writer, S3/MinIO, CDXJ index, dedup |
| 8 | stage-1-security | arq workers, POST /v1/fetch, callbacks |
| 9 | stage-1-security | Archive + usage APIs, admin endpoints |
| 10 | stage-1-security | Prometheus + OTel, structlog, health probes |
| 11 | stage-1-security | pytest + testcontainers, 86 tests |
| 12 | stage-1-security | Dockerfile, CI, Terraform, runbook |
| 13 | stage-13-verification | Full verification, dependency consolidation, cold-start fixes |
| 14 | stage-14-cleanup-performance | Celery removal, arq cron, shared executor, cost metric |
| 15 | stage-15-final | Browser pool, WARC DLQ, streaming archive, rotation flag |

## ADRs (15 documents)

| # | Topic | Status |
|---|---|---|
| 001 | Argon2id key hashing | Accepted |
| 002 | SSRF guard strategy | Accepted |
| 003 | Partitioned request_log | Accepted |
| 004 | Auth dependency chain | Accepted |
| 005 | Rate limiter design | Accepted |
| 006 | Proxy manager design | Accepted |
| 007 | Fetcher architecture | Superseded by ADR-014/015 |
| 008 | WARC storage design | Superseded by ADR-015 |
| 009 | arq job queue | Superseded by ADR-014 |
| 010 | Archive read API | Superseded by ADR-015 |
| 011 | Observability and usage | Accepted |
| 012 | Infra and deployment | Accepted |
| 013 | Verification-driven adjustments | Accepted |
| 014 | Cleanup and performance | Accepted |
| 015 | Final: pool, streaming, DLQ | Accepted |

## Four bugs found only through real execution

1. **structlog recursion OOM** (Stage 13): `_StructlogHandler` bridged stdlib logging
   to structlog, which routed output back into stdlib → infinite JSON nesting →
   4 GB memory in 6 seconds.  Fixed by writing JSON directly to stderr.

2. **arq queue mismatch** (Stage 13): `enqueue_job` defaulted to `arq:queue` but
   `WorkerSettings.queue_name` was `arq:crawler`.  Jobs were never consumed.
   Fixed by passing `_queue_name="arq:crawler"`.

3. **warcio API drift** (Stage 13): `warcio.WARCRecord` constructor was removed;
   the installed version required `RecordBuilder.create_warc_record()`.
   Caught only when the first job ran end-to-end.

4. **WARC filename-before-rotation corruption** (Stage 14-15): `archive()` called
   `_rotate()` (which creates a new writer with a new filename) BEFORE
   `index_record()` captured the filename.  The DB index pointed to an empty,
   un-uploaded file.  Archive reads always 404'd on cold start.  Fixed by
   capturing `self._writer.filename` before rotation.

## Remaining permanent-scope items

| Item | Trigger for revisit |
|---|---|
| Browser pool (full, with idle reaping) | >1 req/s browser-mode |
| Camoufox native support | Residential proxy integration |
| WebSocket/SSE real-time job status | >50 concurrent sync requests |
| httpx connection pooling | >100 req/s sustained |
| geo_proxy_pool.py retirement | When startup sync fully migrated to cron |

## Tags

- v0.1.0 — Verified platform rebuild (Stages 1-13)
- v0.2.0 — Celery removed, arq cron, shared executor, cost metric
- v1.0.0 — Browser pool, streaming archive, WARC DLQ
