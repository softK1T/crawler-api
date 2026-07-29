# Project History — 15-Stage Rebuild

## What this is
A crawler platform (FastAPI + Postgres + Redis + S3/MinIO + arq workers) rebuilt
from an audited legacy codebase in 15 sequential stages between 2026-07-27 and
2026-07-29. Each stage had a frozen scope, an explicit Definition of Done, and an
Architecture Decision Record. This document records what was built, in what order,
and why. Decisions live in `docs/decisions/`; operations live in `docs/runbook.md`.

## Method
Every stage followed the same loop:
1. A written scope naming the files that may change and the files that are frozen.
2. Implementation against a Definition of Done expressed as checkable commands
   (`ruff`, `mypy`, `pytest`, `grep`, HTTP calls) — not prose.
3. An ADR capturing the decision, the alternatives rejected, and the consequences.
4. A report checked against the DoD before the next stage opened.

Freezing modules was the load-bearing part. Security primitives (`app/core/security.py`,
`app/core/url_guard.py`, `app/services/rate_limiter.py`) were frozen after Stage 4
and stayed frozen through Stage 15, so later performance work could not quietly
weaken SSRF validation, authentication, or rate limiting.

## Stages

| Stage | Subject | ADR |
|---|---|---|
| 0 | Audit of the legacy codebase | `audit.md` |
| 1 | Security hardening: Argon2id API keys, SSRF guard, URL policy | ADR-001, ADR-002 |
| 2 | Data model: 9 ORM models, partitioned request_log, Alembic migration | ADR-003 |
| 3 | Authentication: key verification, scopes, test/live, revocation | ADR-004 |
| 4 | Policy resolver + 4-layer sliding-window rate limiter (Lua) | ADR-005 |
| 5 | Proxy manager: weighted picker, health scoring, circuit breaker | ADR-006 |
| 6 | Fetchers: unified Protocol, retry/backoff, header profiles, block detection | ADR-007 |
| 7 | WARC storage: writer, S3/MinIO upload, CDXJ index, dedup, rotation | ADR-008 |
| 8 | Async jobs: arq workers, POST /v1/fetch, callbacks, idempotency keys | ADR-009 |
| 9 | Archive & usage API: WARC retrieval, usage stats, admin endpoints | ADR-010 |
| 10 | Observability: Prometheus metrics, OpenTelemetry traces, structlog JSON logs, health endpoints | ADR-011 |
| 11 | Observability: trace_id propagation, worker instrumentation, usage_counter upsert | ADR-011 |
| 12 | Infrastructure: README, runbook, ADR-012, and verify.sh | ADR-012 |
| 13 | End-to-end verification against running containers | ADR-013 |
| 14 | Cleanup: Celery removal, arq cron proxy sync, shared executor, cost metric | ADR-014 |
| 15 | Durability: WARC dead-letter queue, rotation flag, archive corruption fix | ADR-015 |

## Architecture as shipped

**Authentication.** API keys are hashed with Argon2id. Lookup is O(1) via a unique
key prefix column, so exactly one Argon2 verification runs per request rather than
one per stored key (ADR-001, ADR-004).

**SSRF defence.** Every engine validates every redirect hop before issuing the
request. No engine follows redirects blindly; the fetcher contract enforces this
rather than individual engine authors remembering it (ADR-002, ADR-007).

**Rate limiting.** Sliding windows implemented in Redis Lua across key, app,
domain, and proxy layers. Denials increment `crawler_rate_limit_hits_total` with a
`layer` label (ADR-005).

**Fetchers.** One `FetcherProtocol`, three engines — httpx, curl_cffi (sync, in a
shared thread executor), Playwright. Block detection is a single shared function so
keyword lists cannot drift between engines. Browser mode creates a fresh browser
and a fresh context per fetch (ADR-007, ADR-014).

**Archival.** Responses are written to gzipped WARC files, uploaded to S3/MinIO, and
indexed in `warc_index` with byte offsets. Reads download the full WARC file and
parse it with `warcio.ArchiveIterator` — range-reads over gzip streams are not
valid, so the archive reader fetches the whole file. Deduplication matches on
`(url, sha256)`: the same URL with unchanged content produces a revisit record, and
revisit reads resolve back to the original (ADR-008, ADR-010).

**Jobs.** arq replaced Celery. Job IDs are generated at the API layer and stored
under application-owned Redis keys, decoupling job identity from queue internals.
Idempotency keys are namespaced per application with a 24-hour TTL. Callbacks are
signed with HMAC-SHA256 (ADR-009).

**Observability.** Prometheus metrics, OpenTelemetry traces propagated from API
through enqueue into the worker, and single-line JSON logs carrying `trace_id`,
`job_id`, and `application_id`. `/healthz` is liveness-only and cheap; `/readyz`
checks database, Redis, and S3 client initialisation (ADR-011).

**Usage metering.** `usage_counter` upserts happen in the worker only, never in API
handlers. Failed requests count against quota. A single helper converts bytes to
EUR at a fixed EUR 3.50/GB and feeds both the usage counter and
`crawler_proxy_cost_eur_total`, so the two cannot diverge (ADR-011, ADR-014).

**Durability.** Failed WARC uploads land in a bounded on-disk dead-letter queue and
are retried by a cron function instead of being discarded (ADR-015).

## The four bugs that only real execution found
Stages 13–15 produced little new functionality and disproportionate value, because
running the system surfaced four defects that a green lint, type-check, and test
suite had all missed.

1. **Infinite recursion in the structlog handler.** `_StructlogHandler.emit()`
   re-entered itself, consuming 4 GB in about six seconds. Two earlier reports had
   attributed the resulting unreachable API to environment networking.
2. **Queue name mismatch.** The API enqueued to arq's default queue while the
   worker listened on `arq:crawler`. Jobs accumulated and never executed. Nothing
   errored.
3. **`warcio` API drift.** `warcio.WARCRecord` did not exist in the installed
   version; the writer was migrated to `RecordBuilder`. Without a round-trip read
   the archive could have been structurally broken under a fully green suite.
4. **WARC filename captured after rotation.** `archive()` rotated the writer —
   creating a new file with a new name — before `index_record()` captured the
   filename. The database row pointed at an empty object while the bytes lived
   under a different key. Metadata looked valid; bodies were unreachable. This was
   silent archive corruption, invisible until a real S3 read.

The pattern is consistent: all four were integration and lifecycle faults, in the
seams between components rather than inside them. Static analysis and unit tests
are structurally unable to see them.

## Verification contract
`scripts/verify.sh` performs the full write path against running containers:
authenticate, fetch, poll to completion, assert an archive entry exists, read the
body back through the S3, and assert usage counters advanced. Two rules were
learned the hard way and are now permanent:
- the archive assertion retries until an entry exists and never passes on an empty
  list — a passing assertion on zero rows is how corruption hides;
- a warm run does not satisfy the cold-start requirement, because CI is always cold.
The gate is two consecutive cold runs (`docker compose down -v` between them) both
exiting 0.

## Releases
| Tag | Contents |
|---|---|
| `v0.1.0` | Verified rebuild, Stages 1–13 |
| `v0.2.0` | Celery removed, arq cron proxy sync, shared executor, cost metric |
| `v1.0.0` | WARC dead-letter queue, rotation flag, archive corruption fix |

`v1.0.0` was retagged once. Its first annotation advertised a browser pool and a
streaming archive endpoint that were designed but not implemented. The tag now
describes the code. This is recorded because it is exactly the kind of drift that
misleads a reader six months later.

## Deferred, with triggers
Nothing is deferred to "a future stage". Each open item carries the condition that
forces it:

| Item | Trigger |
|---|---|
| Bounded browser pool | sustained >1 req/s in browser mode (ADR-007) |
| Chunked streaming archive endpoint | archived bodies >10 MB (ADR-010) |
| Camoufox engine | residential proxy support lands (ADR-007) |
| WebSocket/SSE job notifications | >50 concurrent sync requests (ADR-009) |
| httpx connection pooling | >100 req/s sustained (ADR-007) |

One invariant must survive into any future browser pool: **reuse browsers, never
contexts.** A shared context leaks cookies and storage between tenants. Two tests
guard it today — cookie isolation across sequential fetches, and per-fetch SSRF
interception — and both must be carried into the pooled implementation rather than
rewritten (ADR-014).
