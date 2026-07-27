# ADR-005: 4-Layer Rate Limiter Design

**Status:** Accepted
**Date:** 2026-07-27
**Stage:** Stage 4 — Policy Resolver + Rate Limiter

## Context

A multi-tenant crawling platform needs to enforce usage limits at multiple
granularities: per API key (RPS), per application (monthly quota), per target
domain (politeness), and per proxy (cooldown). Without rate limiting, one
misconfigured client can exhaust the monthly quota for all tenants or overwhelm
a target domain, triggering IP bans.

## Decision

### 4-layer sliding-window model

| Layer | Key | Window | Purpose |
|---|---|---|---|
| L1 — key | `rl:key:{prefix}` | 60s | Per-key requests per minute |
| L2 — app | `rl:app:{app_id}` | 30 days | Per-application monthly quota (rolling) |
| L3 — domain | `rl:dom:{domain}` | 1s | Global politeness to a single domain |
| L4 — proxy | `rl:proxy:{proxy_id}` | 1s | Per-proxy cooldown |

Layers are checked in order (L1→L2→L3→L4). The first layer that denies
returns immediately; the remaining layers are not charged.

### Sliding-window via Redis sorted sets (ZSET)

Each rate-limit key is a Redis sorted set. Each request inserts a unique
member with a score of `now_ms`. A Lua script atomically:

1. Removes members with scores older than `now_ms - window_ms` (ZREMRANGEBYSCORE)
2. Counts remaining members (ZCARD)
3. If count + cost > limit → returns `{0, count, reset_at_ms}` (denied)
4. Otherwise → adds new member, sets TTL, returns `{1, count+1, now+window_ms}`

A Lua script is necessary because the cleanup + count + insert must be atomic.
Redis MULTI/EXEC cannot express the conditional logic (read → evaluate → write).

### Sliding-window vs token bucket

Token bucket was rejected because:
- Token bucket refills linearly over time; at the start of a window, a burst
  can consume the entire quota instantly.
- Sliding window enforces a hard ceiling within the window regardless of
  burst timing.
- For a crawling platform, steady rate is more important than burst tolerance
  (bursts trigger target-site rate limiting and IP bans).

### Monthly rolling window (L2), not calendar month

A 30-day rolling window avoids the "reset at midnight on the 1st" behavior.
If a client uses their entire quota on day 30, they must wait 30 days for
the oldest requests to expire. This prevents end-of-month quota races.

### Redis-down → fail-open

If Redis is unreachable during a rate-limit check, the limiter returns an
`allowed=True` result and logs a warning. Rationale:

- A Redis outage should not block all crawling. The probability of Redis
  being down while the rest of the stack (API, DB, Celery) is up is low but
  non-zero.
- Rate limits are a soft enforcement mechanism, not a security boundary.
  API key auth already gates access.
- If an attacker DDoS's the platform to trigger fail-open, the Redis outage
  itself is the availability incident — rate limits are secondary.

### X-RateLimit headers

Successful fetch requests include:
- `X-RateLimit-Limit`: configured limit for the most restrictive layer
- `X-RateLimit-Remaining`: remaining capacity
- `X-RateLimit-Reset`: Unix epoch seconds when the window resets

These use the L3 (domain) result, which is the most informative for callers
tuning their crawl politeness.

## Alternatives Considered

### Token bucket (Redis INCR + TTL)
- **Rejected:** Increment-and-check (`INCR key; EXPIRE key window`) is simpler
  but allows the entire quota to be consumed in a burst at window start.

### Fixed-window counter
- **Rejected:** The "reset moment" creates a race condition where a client can
  double their quota by straddling the window boundary.

### Rate limiting in application middleware (not Redis)
- **Rejected:** Multiple API workers (behind a load balancer) would each have
  independent counters. Redis is the shared state.

## Consequences

- **Positive:** Atomic enforcement across all API workers. Lua script is ~20
  lines, auditable, and deterministic.
- **Positive:** Fail-open ensures availability during Redis incidents.
- **Negative:** Redis sorted sets grow linearly with request rate. TTL
  cleanup is lazy (ZREMRANGEBYSCORE on next request). For 1000 requests/s,
  the ZSET holds ~60K members at any time — well within Redis memory limits.
- **Negative:** Lua script debugging requires `redis-cli --ldb`. Unit tests
  for the script itself need a real Redis instance (testcontainers fixture
  exists in tests/conftest.py).
