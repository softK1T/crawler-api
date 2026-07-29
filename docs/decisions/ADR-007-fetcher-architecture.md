# ADR-007: Unified Fetcher Architecture

**Status:** Accepted
**Date:** 2026-07-27
**Stage:** Stage 6 — Fetchers

## Context

Stage 0–5 used three independent crawler implementations: `Crawler` (httpx,
sync, in `app/services/crawler.py`), `StealthCrawler` (curl_cffi, sync),
and standalone async functions for Playwright/Camoufox. Each had different
interfaces, different error handling, and different block detection logic.
Some validated SSRF per-hop; others did not.

Stage 6 introduces a unified `FetcherProtocol` so that the retry loop, header
building, proxy selection, and health reporting are implemented once and shared
across all engines.

## Decision

### One Protocol, three engines

```
FetcherProtocol
├── HttpxFetcher      (httpx.AsyncClient, per-hop SSRF via manual redirect)
├── CurlFetcher       (curl_cffi, sync in ThreadPoolExecutor)
└── PlaywrightFetcher (Playwright async API, browser-per-fetch)
```

Each engine implements `async def fetch(url, *, proxy, headers, timeout_s,
follow_redirects, max_redirects) -> FetchResult`.

### Per-hop SSRF, mandatory for all engines

All three engines use `allow_redirects=False` internally and handle redirects
in a manual loop. Every hop calls `validate_url_async` (or `validate_url_sync`
for curl_cffi) before the request. This is non-negotiable — no engine is
allowed to follow redirects blindly.

### Per-fetch client / browser (no pooling)

- **HttpxFetcher** creates a new `httpx.AsyncClient` per call. Connection pool
  sharing is sacrificed for proxy isolation. At current scale (~100 req/s), the
  TCP connection overhead is acceptable.
- **CurlFetcher** creates a new `ThreadPoolExecutor(max_workers=1)` per call.
  In production, a shared executor should be used — documented as a deferred
  optimization.
- **PlaywrightFetcher** launches a new browser per call. Browser reuse/pooling
  is deferred. At current scale (browser mode is rare, ~1 req/s), this is
  acceptable.

### CurlFetcher executor: ThreadPoolExecutor, not gevent

curl_cffi is synchronous. The legacy code used `asyncio.run()` inside Celery
gevent workers, which the audit flagged as a deadlock risk. CurlFetcher runs
sync curl_cffi in a `ThreadPoolExecutor`, avoiding the gevent+asyncio conflict
entirely.

### Block detection: shared `_detect_block` function

A single module-level function in `base.py` takes `(status_code, body_bytes)`
and returns `(blocked, reason)`. Keywords are case-insensitive substrings on
the first 64KB of the body only. All three fetchers call the same function.

Maintenance: add new keywords to `_BLOCK_KEYWORDS` dict in `base.py`. The dict
maps keyword → category ("captcha", "bot_detection", "ip_ban").

### Camoufox deferred

The legacy code supported a "camoufox" engine mode. `get_fetcher("camoufox")`
returns `PlaywrightFetcher` with a warning log. Native Camoufox support
(anti-detect Firefox with device fingerprint randomization) is deferred to
a future stage when residential proxy support is added.

## Alternatives Considered

### Async context manager for shared client pool
- **Rejected for now:** Proxy isolation per request is more important than
  connection reuse. A shared pool with proxy rotation would require per-request
  proxy injection, which httpx's connection pooling doesn't support cleanly.

### Block detection as a per-engine method
- **Rejected:** Keyword maintenance would drift between engines. A shared
  function ensures all engines benefit from updated keyword lists.

## Consequences

- **Positive:** Adding a new block keyword in `_BLOCK_KEYWORDS` updates all
  three engines immediately.
- **Positive:** `fetch_with_retry` orchestrates proxy selection, health
  reporting, and jittered backoff once — engine implementations are thin.
- **Positive:** Per-hop SSRF is guaranteed by the fetcher contract, not by
  individual engine authors.
- **Negative:** Per-fetch client creation adds ~10-50ms overhead for TCP
  handshakes. At 100+ req/s sustained, connection pooling should be revisited.
- **Negative:** Three engines mean three places where new features (e.g.,
  custom TLS fingerprinting) must be implemented. The Protocol interface
  minimizes drift but doesn't eliminate it.

## Stage 14 update (ADR-014)

The shared-executor and browser-pooling items deferred in this ADR were
addressed in ADR-014 (Stage 14):
- Shared executor: implemented (module-level ThreadPoolExecutor, max_workers=8)
- Browser pool: re-deferred to Stage 15 with context-isolation invariant
  (pool reuses browsers, never contexts — ADR-014 §5)
