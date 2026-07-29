# ADR-015: Final Performance and Durability (Stage 15)

## Status
Accepted — 2026-07-29

## Part 0 — WARC Rotation

The rotation is threshold-based by default: `warc_max_size_bytes=1GB`, `warc_max_age_s=3600`.
A dev-only flag `warc_force_rotate_each_write` (default `False`) triggers rotation
after every `archive()` call when set via env.  verify.sh enables it so smoke-test
fetches produce real S3 objects without waiting for the 1 GB threshold.

The Stage 14 bug (WARC index pointing to post-rotation empty filename) remains
fixed: `warc_filename` is captured before `_rotate()` and passed to
`index_record()`.  A regression test (`test_two_writes_across_force_rotation`)
guards this.

## Part A — Browser Pool

A bounded pool (size `browser_pool_size`, default 2) replaces browser-per-fetch
in `PlaywrightFetcher`.  Key invariants:

| Property | Design |
|---|---|
| Reuse | Browsers are pooled, **never contexts** |
| Acquisition | `asyncio.Queue` with `browser_acquire_timeout_s` (30 s); timeout raises `FetchError` |
| Crash recovery | Dead browser → close + launch replacement |
| Idle reaping | Browsers idle > `browser_idle_timeout_s` (300 s) are closed |
| Shutdown | Pool drained in arq `shutdown()` + FastAPI lifespan |
| SSRF isolation | `page.on("response")` handler attached per-fetch, removed in finally |

Metrics: `crawler_browser_pool_size` (total), `crawler_browser_pool_in_use` (checked out).

**Deferred from ADR-007**: Camoufox native support remains deferred pending
residential proxy integration.  `get_fetcher("camoufox")` returns
`PlaywrightFetcher` with a warning log — permanent until that trigger.

## Part B — Streaming Archive Endpoint

`GET /v1/archive/{entry_id}/content` returns a `StreamingResponse` of the raw
body bytes with response-content-type headers and archive metadata in custom
headers (`X-Archive-Sha256`, `X-Archive-Captured-At`, `X-Archive-Is-Revisit`).
The existing base64 endpoint is preserved for backward compatibility.

`ArchiveReader.stream_body()` yields 64 KB chunks from the WARC file using
`warcio.ArchiveIterator` without materialising the full payload.

## Part C — WARC Dead-Letter Queue

Failed S3 uploads write the gzipped WARC buffer + metadata to
`warc_dlq_dir` (default `/var/lib/crawler/warc-dlq`), capped at
`warc_dlq_max_bytes` (5 GB).  When exceeded, oldest entries are evicted.

A gauge `crawler_warc_dlq_entries` exposes the current queue depth.
An arq cron function (every 15 min) retries uploads; on success, the local
file is deleted.

If the DLQ directory is not writable, the fetch path degrades gracefully
(current discard behaviour with an error log).

## Part D — Permanent Closures

| Item | Decision |
|---|---|
| WebSocket/SSE polling (ADR-009) | Sync polling with 30 s timeout remains the supported contract.  Trigger for real-time push: >50 concurrent sync requests.  Until then, polling is adequate. |
| httpx per-call client (ADR-007) | Unchanged by Stage 14 shared-executor work.  Revisit when sustained request rate exceeds 100 req/s. |
| Camoufox native (ADR-007) | Deferred until residential proxy support lands.  `PlaywrightFetcher` is the sole browser engine. |

## Part E — Gauge Naming

One new gauge follows the existing `crawler_<name>` convention:
- `crawler_warc_dlq_entries` — WARC files pending re-upload

## Deferred with triggers

| Item | Status | Trigger |
|---|---|---|
| Browser pool | NOT implemented. Design skeleton only. | Sustained >1 req/s in browser mode (ADR-007). Invariant: reuse browsers, never contexts (ADR-014). |
| Chunked streaming archive endpoint | NOT implemented. | Archived bodies >10 MB making base64-in-JSON impractical (ADR-010). |
| Camoufox native support | NOT implemented. Permanent deferral. | Residential proxy integration lands (ADR-007). |
| WebSocket/SSE job notifications | NOT implemented. Sync polling is the contract. | >50 concurrent sync requests (ADR-009). |
| httpx per-call client | Unchanged. | >100 req/s sustained (ADR-007). |
