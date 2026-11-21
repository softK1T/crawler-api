# Design decisions

[Documentation](README.md) · [Architecture](ARCHITECTURE.md) · [Security boundaries](SECURITY.md)

The architecture page says what the parts are. This page says why they are built this way: the decision, the reason, the option that was turned down, and the limit that comes with it. It describes the code on `master`, not its history.

## A queue between the API and the fetch

- **Decision.** `POST /v1/fetch` creates the job id, enqueues the job on Redis (arq) and answers 202. A separate worker process runs it. Job state and the result live in Redis under the job id and expire after 24 hours. arq keeps no results of its own and never retries a job.
- **Why.** A fetch takes from a fraction of a second to minutes and may start a browser, so it cannot run inside the request. Retries belong to the fetch loop, which knows about proxies and tiers; a queue-level retry would fetch the page twice.
- **Turned down.** Celery: its gevent workers did not mix with asyncio browser engines.
- **Limit.** `"options": {"sync": true}` is a server-side poll, not a push channel: after 30 seconds the client gets 202 and polls. Results are gone after the TTL; only the attempt log and, when enabled, the WARC archive stay.
- **Code.** `app/api/v1/endpoints/jobs.py`, `app/services/job_service.py`, `app/worker/arq_worker.py`

## Four engines behind one contract

- **Decision.** HTTPX, curl_cffi, Playwright (Chromium) and Camoufox (Firefox) implement one `fetch()` protocol and return one `FetchResult`. One loop, `fetch_with_retry`, owns proxy choice, health reports, backoff and escalation. One function, `detect_block_reason`, classifies blocks for every engine.
- **Why.** Separate crawlers per engine had drifted apart in error handling, block detection and URL checks.
- **Turned down.** Block detection inside each engine.
- **Limit.** Only high-confidence block pages are flagged: 429, Cloudflare challenge markers, CAPTCHA widgets, IP-ban and WAF texts, and 401, 403, 407, 451. A 200 page that is an empty JavaScript shell counts as a success, which is why `scripts/benchmark.py` checks each body for marker text.
- **Code.** `app/services/fetchers/`, `app/services/block_detector.py`

## The escalation ladder

- **Decision.** Tiers are ordered by cost: 0 HTTPX direct, 1 HTTPX with a datacenter proxy, 2 curl_cffi with datacenter, 3 curl_cffi with residential, 4 Playwright with residential, 5 Camoufox with residential, 6 Camoufox with mobile. A job starts at the tier learned for the domain; a request for a mode other than `static` starts at the first tier of that engine. It steps up when a block looks like an anti-bot challenge (`cloudflare`, `waf`, `captcha`) and the attempts for the tier are used, or at once when a direct connection is blocked. `ip_ban` and `rate_limited` behind a proxy only rotate the proxy. Attempts per job are capped at 12 by default.
- **Learning.** The tier that was needed is stored on the domain policy row: a block raises it, a success lowers it by at most one. `"use_policy": false` skips the policy and the learning for one job.
- **Why.** The right engine for a domain cannot be configured by hand for hundreds of domains, so the cheapest one that works is found at run time and remembered.
- **Turned down.** A shorter ladder that merges the two curl_cffi tiers: it loses the datacenter or residential split, the main cost gate. A separate table for learned tiers.
- **Limit.** With premium proxies off, the default, residential and mobile tiers run on datacenter proxies and tiers that would repeat an engine are skipped: 0, 1, 2, 4, 5. An active domain policy's engine wins over the request mode.
- **Code.** `app/services/escalation.py`, `app/services/fetchers/base.py`, `app/services/policy_learner.py`

## Browser engines

- **Decision.** Playwright keeps one long-lived Chromium and opens a fresh context per job, so no cookies or storage pass between jobs; a semaphore caps concurrency. Camoufox starts a fresh Firefox per request under its own semaphore. `wait_for` is a CSS selector both wait for before they read the page.
- **Why.** Chromium's start cost is paid once and contexts give isolation. Camoufox is the last and rarest tier, so a second pool was not worth its lifecycle code.
- **Turned down.** Reusing contexts between jobs. A Camoufox pool.
- **Limit.** Every Camoufox request pays a browser start. The shipped stack runs one browser job of each kind at a time (`BROWSER_POOL_SIZE`, `MAX_CONCURRENT_CAMOUFOX`).
- **Code.** `app/worker/browser_pool.py`, `app/services/fetchers/playwright_fetcher.py`, `app/services/fetchers/camoufox_fetcher.py`

## Outbound URL guard

- **Decision.** Every URL is checked right before the request and again on every redirect hop, with a fresh DNS lookup each time: only `http` and `https`, only ports 80, 443, 8080 and 8443, and no host with any address in a private or reserved range. The HTTP engines follow redirects by hand to do this. The browser engines intercept every request a page makes and close WebSockets.
- **Why.** A service that fetches URLs given by clients is a way into the internal network and cloud metadata. Checking at request time narrows the DNS rebinding window.
- **Turned down.** Caching DNS answers, even briefly: any cache reopens that window.
- **Limit.** Behind a proxy the browser sends requests itself, so the site sees a real browser's TLS fingerprint (`BROWSER_NATIVE_TRANSPORT`, on by default). In that mode redirect hops are not checked again; they leave through the proxy.
- **Code.** `app/core/url_guard.py`, `app/services/fetchers/browser_guard.py`

## API keys and scopes

- **Decision.** Keys are stored as argon2id hashes; the first 8 characters are a lookup prefix. Scopes (`fetch`, `archive`, `admin`, `keys`) match exactly, with no hierarchy, and each endpoint names the scope it needs through a FastAPI dependency. Only a key with the `keys` scope issues keys, and it can grant only scopes it holds. Rotation mints a successor and leaves the old key valid for 24 hours.
- **Why.** A leaked database must not expose keys. A dependency can state a scope per endpoint; middleware runs before route matching and cannot.
- **Turned down.** bcrypt and plain SHA-256. Middleware authentication. Self-service signup.
- **Limit.** Archive and administration routes are not separated by application, so those scopes are for trusted operators only.
- **Code.** `app/core/security.py`, `app/api/v1/dependencies.py`, `app/services/key_service.py`

## Rate limits

- **Decision.** Sliding windows in Redis sorted sets, each check one atomic Lua script: per key over 60 seconds, per application over 30 days, per target domain over 1 second, or over a longer window when the policy rate is below 1 request a second. A denial is a 429 with `Retry-After`. If Redis fails, the limiter lets the request through.
- **Why.** Limits must hold across API processes and allow no burst at a window boundary. A broken limiter should not stop fetching.
- **Turned down.** Token bucket and fixed-window counters.
- **Limit.** The domain limit comes from the domain policy and is 1 request a second without one, so a client that submits faster to one domain must wait and retry.
- **Code.** `app/services/rate_limiter.py`

## Proxies

- **Decision.** A proxy is picked by weighted random on its health score among active proxies that match the requested country, city and type and are not cooling down. A transport failure lowers the score and starts a cooldown that doubles from 60 seconds to 1 hour. A refusal by the target site does not count against the proxy: it is only set aside for that domain and engine for 10 minutes. A per-domain circuit breaker stops handing out proxies after repeated failures. A job that needs a proxy and has none fails with `PROXY_POOL_EMPTY` or `PROXY_POOL_EXHAUSTED`.
- **Why.** Health-aware choice quarantines bad proxies without an operator. A site block says nothing about the proxy elsewhere. A silent direct connection hid why a job ran without a proxy.
- **Turned down.** Round-robin and least-connections: no health signal. Falling back to a direct connection.
- **Limit.** Provider sync is off in the compose stack and needs a provider API key. When on, it upserts the provider's list and deactivates proxies that disappeared, never deletes them.
- **Code.** `app/services/proxy_manager.py`, `app/services/proxy_health.py`, `app/services/proxy_sync_service.py`

## Attempt log

- **Decision.** `request_log` holds one row per transport attempt, not per job. It is range-partitioned by year on `requested_at`, with a default partition. It never stores headers, bodies, cookies or proxy credentials.
- **Why.** Index scans stay bounded and old data is removed by dropping a partition, not by a large delete.
- **Turned down.** Monthly partitions: too many of them. TimescaleDB.
- **Limit.** Yearly partitions exist for 2026 and 2027. Later rows go to the default partition until a migration adds the year.
- **Code.** `app/models/request_log.py`, `alembic/versions/`

## WARC archive

- **Decision.** Each capture is one WARC object. It is written to a fsynced local journal first, then uploaded to S3, then indexed in PostgreSQL, and only then removed from the journal. A cron replays what is left. A full journal refuses new captures and deletes nothing. The archive keeps the bytes the server sent.
- **Why.** An S3 outage or a worker crash must not lose a capture.
- **Turned down.** One large in-memory WARC rotated by size and age: a crash or a failed upload lost the whole buffer.
- **Limit.** A job whose archive step fails is marked failed even though the capture is delivered later. Blocked responses are not archived.
- **Code.** `app/services/warc/storage.py`, `app/worker/tasks/fetch_task.py`

## Response bodies

- **Decision.** The worker decompresses the body (gzip, deflate, brotli, zstd) before it stores the result, and reports the original encoding, the size and a SHA-256.
- **Why.** Clients used to get bodies that might or might not be compressed, with headers that did not match.
- **Turned down.** Leaving decompression to every client.
- **Limit.** If decoding fails, the body is returned as received. A body over 10 MiB fails the job.
- **Code.** `app/services/content_decoder.py`, `app/worker/tasks/fetch_task.py`

## Readiness and metrics

- **Decision.** `/healthz` is liveness. `/readyz` answers ready only when PostgreSQL, Redis, a recent worker heartbeat with a working Chromium and, when required, Camoufox, and S3 when WARC is on, all pass.
- **Why.** An API that accepts jobs no worker can run is not ready.
- **Turned down.** Prometheus multiprocess mode, which needs a directory shared by the API and worker containers, and relaying counters through Redis: a second scrape target is simpler.
- **Limit.** Each process exports only its own counters. `/metrics` on the API shows API latency, rate-limit denials and queue depth. Blocks, attempts per tier, WARC bytes and proxy cost are counted in the worker, which serves them on its own port (`WORKER_METRICS_PORT`, 9464 in Compose, host loopback only). A Prometheus scraper reads both.
- **Code.** `app/api/v1/endpoints/health.py`, `app/core/observability.py`, `app/worker/tasks/fetch_task.py`

## One host, one image

- **Decision.** Docker Compose on a single host. API and worker share one image with both browsers inside. The API port is bound to loopback.
- **Why.** One maintainer and a small budget.
- **Turned down.** Kubernetes, until there is more than one node. The Playwright base image: a second base image and less control over browser versions.
- **Limit.** Linux amd64 only. The stack is built for loopback use and has had no public-production security audit.
- **Code.** `docker-compose.yml`, `Dockerfile`

## Left out on purpose

| Not built | Revisit when |
|---|---|
| WebSocket or SSE push for results | More than about 50 synchronous waits at once |
| A shared HTTPX client | A sustained rate above about 100 requests a second |
| An HTML fallback in the OLX parser | The embedded listing data stops being complete; see below |

The OLX parser in `scripts/olx_parser.py` reads the listing data embedded in the page and stops with an error when it is missing. The alternatives were checked on 37 saved listing pages: JSON-LD offers exist only on the first page of some categories and cover 20 of 52 ads there, and the HTML cards carry no region, seller type or exact timestamps. A fallback would fill a dataset with rows that silently miss fields.
