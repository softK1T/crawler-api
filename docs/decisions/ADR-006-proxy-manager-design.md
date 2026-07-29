# ADR-006: Proxy Manager Design

**Status:** Accepted
**Date:** 2026-07-27
**Stage:** Stage 5 — Proxy Manager

## Context

Stage 0–2 used `SmartProxyPool` / `GeoProxyPool` in `app/services/crawler.py` and
`app/services/geo_proxy_pool.py` — in-memory proxy pools loaded from a text file,
with synchronous per-proxy rate limiting via Redis. These work for a single
process but cannot scale to multiple API workers, lack circuit breaker semantics,
and don't persist health state to the database.

Stage 5 introduces `ProxyManager` as the canonical proxy service for all new code
paths, backed by the `proxies` table (defined in Stage 2 migration 0001) and Redis
for ephemeral circuit-breaker + sticky-session state.

## Decision

### 1. Weighted random selection by health score

```
eligible = [p for p in all_proxies if not on_cooldown(p)]
weights = [max(0.01, p.health_score) for p in eligible]
selected = random.choices(eligible, weights=weights, k=1)[0]
```

- **Weight floor (0.01):** A proxy with `health_score = 0.0` still has a small
  chance of being selected (canary testing — if it recovers, the score climbs).
- **Non-crypto random:** `random.choices()` is used intentionally. Proxy
  selection does not require cryptographic randomness; speed is the priority.
- **All eligible proxies are loaded into memory:** At ~200 proxies per pool,
  this is <10KB of data and a single SELECT query.

### 2. Exponential-backoff cooldown

On each failure, `consecutive_failures` is incremented and a cooldown is computed:

```
cooldown_s = min(60 * 2^(failures-1), 3600)
```

| Failures | Cooldown |
|---|---|
| 1 | 60s |
| 2 | 120s |
| 3 | 240s |
| 4 | 480s |
| 5 | 960s |
| 6+ | 1800s (capped at 3600) |

Integer exponentiation is used (`2 ** n`) to avoid floating-point precision
issues. On success, `consecutive_failures` resets to 0 and cooldown is cleared.

### 3. Per-domain circuit breaker (Redis, ephemeral)

A domain-level circuit breaker prevents the platform from hammering a target
site that is consistently returning errors. Unlike the proxy cooldown, the
circuit breaker lives in Redis (not the DB) because:

- It's ephemeral — if Redis restarts, losing circuit state is acceptable
  (fail-open).
- It's fast — a Redis GET per request vs a DB round-trip.
- It's global across all API workers.

Threshold: 5 consecutive domain failures within 600s → circuit opens for 300s.
After 300s, a single probe request is allowed ("half-open"). If it succeeds,
the circuit closes; if it fails, it re-opens for another 300s.

### 4. Sticky sessions (Redis, 30-min TTL)

When `sticky_key` is provided (e.g., a job_id), the selected proxy is pinned:

```
SET sticky:{domain}:{sticky_key} = proxy_id EX 1800
```

On subsequent requests with the same sticky_key, the pinned proxy is returned
if it's still healthy. If it's on cooldown, the sticky key is deleted and a
fresh proxy is selected.

### 5. Health persistence

Health scores are written to the `proxies` table on every request via
`UPDATE ... SET health_score = ..., consecutive_failures = ..., cooldown_until = ...`.
At 100 req/s this is 100 writes/s — acceptable for the current hardware target.
At 1000+ req/s, consider batching writes (a deferred optimization, documented
as a scaling note).

### 6. proxy.url never exposed

`ProxyResponse` intentionally omits the `url` field. Proxy credentials
(`http://user:pass@host:port`) are stored in the `proxies.url` column but
must never appear in API responses, log lines, or error messages. This is
the application-layer enforcement of the DB-level risk documented in ADR-003.

## Alternatives Considered

### Round-robin or least-connections
- **Rejected:** No signal about proxy health. A failing proxy would get equal
  traffic until manually removed.

### Store circuit breaker state in the database
- **Rejected:** Would add a DB round-trip to every proxy selection. Redis
  provides sub-millisecond reads for ephemeral state.

### Proxy health as a background job (not per-request write)
- **Rejected for now:** Adds complexity (batch writes, staleness window). The
  per-request write is acceptable at current scale. Documented as a future
  optimization.

## Consequences

- **Positive:** Proxy selection is health-aware and self-correcting. Failed
  proxies are automatically quarantined.
- **Positive:** Circuit breaker prevents target-site retaliation (IP bans).
- **Positive:** Proxy credentials are never leaked via API responses.
- **Negative:** Per-request DB writes to the proxies table. At high scale
  (>1000 req/s), batch writes with a background flusher are recommended.
- **Negative:** Legacy `GeoProxyPool` and `proxy_singleton` remain in the
  codebase for backward compat with Celery workers. Two proxy selection
  paths exist — migration of worker tasks to ProxyManager is Stage 8.
