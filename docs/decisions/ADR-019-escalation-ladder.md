# ADR-019: Escalation Ladder Design

**Date:** 2026-08-04  
**Status:** Accepted

## Context

700 heterogeneous domains across 6-7 anti-bot vendors. Hand-configuring each
is not viable. The system must discover the right fetch strategy at runtime.

## Decision

A 7-tier ordered ladder (index = escalation_tier) from cheapest to most
expensive. The retry loop in `fetch_with_retry` walks up the ladder when a
block reason is in `ESCALATABLE`.

| Tier | Engine     | Proxy type  | Note                        |
|------|------------|-------------|-----------------------------|
| 0    | httpx      | direct      | ~60% of open domains        |
| 1    | httpx      | datacenter  | IP rotation only            |
| 2    | curl_cffi  | datacenter  | TLS fingerprint bypass      |
| 3    | curl_cffi  | residential | Defeats datacenter IP bans  |
| 4    | playwright | residential | Interactive JS challenges   |
| 5    | camoufox   | residential | Strongest browser camouflage|
| 6    | camoufox   | mobile      | Last resort (JD, Shopee)    |

Tiers 3-6 are gated behind `ENABLE_PREMIUM_PROXY_TIERS=false` by default
(residential/mobile proxies are not yet purchased).

## ESCALATABLE vs rotation-only

- **Escalate** (new engine/proxy_type): CLOUDFLARE, WAF, CAPTCHA, vendor challenges.
  These mean the *technique* is wrong, not just the IP.
- **Rotate** (same engine, new proxy): RATE_LIMITED, IP_BAN, OTHER.
  These mean the *IP* is wrong; changing engine would waste money.

## Prometheus cardinality

`escalation_tier_current` labelled by `domain` (700 series) is within
Prometheus's practical ceiling. `fetch_attempts_by_tier` is labelled by
`tier` + `engine` only (42 series max) to avoid explosion.

## max_retries vs max_escalation_attempts

`max_retries=3` (default) is insufficient to traverse tiers with
`MAX_ATTEMPTS_PER_TIER=2`. Added `max_escalation_attempts=12` to
`DomainPolicy`. `max_retries` retains its meaning as per-tier attempt cap
for non-escalating callers.

## Rejected alternatives

- **5-tier ladder**: merging curl_cffi tiers would lose the residential/datacenter
  split that is the primary cost gate. Rejected.
- **Escalation above fetch_with_retry**: would require duplicating retry-state
  machinery. Rejected — extend existing state machine instead.
- **Learning state in separate table**: adds a JOIN per request with no benefit
  at this scale. Rejected until >10k domains.
