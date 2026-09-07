# ADR-020: Camoufox packaging and lifecycle decision

## Status
Accepted

## Context
LADDER tiers 5-6 require camoufox (Firefox + humanization) to defeat
Akamai Bot Manager and Kasada, which fingerprint Chromium-specific JS APIs.
camoufox[geoip]>=0.4.0 is already in pyproject optional-deps [browser].

## Decision

### Per-request lifecycle (not pool reuse)
BrowserPool wraps a single long-lived Chromium process (Playwright).
camoufox uses Firefox, which cannot share that pool.  Options considered:

1. Separate CamoufoxPool mirroring BrowserPool — adds ~150 LOC,
   complex lifecycle management, uncertain gain since tiers 5-6 are rare.
2. Per-request launch — simpler, ~1-2 s cold-start overhead acceptable
   because camoufox is only reached after 4 cheaper tiers failed.

We chose (2).  A module-level asyncio.Semaphore caps concurrency at
MAX_CONCURRENT_CAMOUFOX (default 2) to prevent OOM on the worker pod.

### Lazy import
`import camoufox` is inside the fetch method so the API image (which
does not install [browser]) imports cleanly without ImportError.

### Docker: no separate image (yet)
Dockerfile already installs [browser] deps including camoufox.
The Firefox binary is fetched at Docker build time via
`python -m camoufox fetch` in the Dockerfile, not at container start.
A split worker/API image is deferred until the EUR 20/month budget is
visibly strained.  The worker runs on a single t3.small (~€9/month);
adding camoufox does not require a second instance because tiers 5-6 are
rare cold-start paths.

> **Status update (post-audit):** the original claim that the Firefox binary
> was "already pulled by `camoufox-install` at container start via the
> existing entrypoint pattern" was inaccurate — no `camoufox-install` step
> existed anywhere in the Dockerfile, docker-compose.yml, or worker
> entrypoint. Fixed by an explicit
> `RUN python -m camoufox fetch` build step in the Dockerfile
> runtime stage (plus Xvfb for `headless="virtual"` launches; the
> `--browserforge` flag does not exist in this CLI — variant selection is
> version-prefix based), a non-fatal
> `verify_camoufox()` startup self-check (`app/worker/camoufox_check.py`,
> called from `fetch_task.startup()`), and the `mode="camoufox"` →
> camoufox engine mapping in `fetch_task.py` (it previously resolved to the
> Playwright/Chromium engine).

### GeoIP alignment
When proxy.country is set, `country=` is passed to AsyncNewBrowser so
camoufox[geoip] selects matching locale/timezone.  Geo-mismatch is a
detection signal for Akamai.

## Consequences
- CamoufoxFetcher is fully self-contained; no changes to BrowserPool.
- MAX_CONCURRENT_CAMOUFOX=2 is a tunable env var.
- If camoufox is not installed, FetchError is raised with a clear message.
- Tiers 5-6 now work end-to-end; the ladder is fully operational.
