# ADR-002: SSRF Guard Strategy — Per-Hop Validation

**Status:** Accepted
**Date:** 2026-07-27
**Stage:** Stage 1 — Security Hardening

## Context

A crawler fetches arbitrary URLs submitted by API clients. Without outbound URL
validation, a malicious or compromised client can:

1. Scan internal infrastructure (`http://10.0.0.5:8080/`)
2. Access cloud metadata services (`http://169.254.169.254/latest/meta-data/`)
3. Exploit DNS rebinding: resolve `evil.com` → public IP during validation,
   then re-resolve → `127.0.0.1` during fetch (TOCTOU)

The audit (Stage 0) found that some code paths validated only the initial URL
and then followed redirects blindly (`allow_redirects=True` in curl_cffi,
`follow_redirects=True` in the old httpx code).

## Decision

### 1. Per-hop validation — every URL, every time

Every outbound request path must validate the target URL *immediately before*
the network call, and re-validate after every redirect. DNS results are never
cached between hops. This closes the TOCTOU window: if a rebinding attack
changes the DNS answer between validation and fetch, the attacker must win a
race within a single event-loop tick.

Implementation:

- **httpx path** (`app/services/crawler.py`): Already implemented in
  `_get_guarded()` — calls `validate_url_sync(current)` for every hop.
- **curl_cffi path** (`app/services/stealth_crawler.py`): Fixed in Stage 1.
  Changed from `allow_redirects=True` to `allow_redirects=False` with a manual
  redirect loop that calls `validate_url_sync()` before each request.
- **Browser paths** (Playwright/Camoufox): Browser engines manage their own
  redirects internally. These paths accept only URLs that have passed
  `validate_url_sync` before browser launch. Browser-level redirects are
  assumed safe (no known rebinding vector in Chromium/Gecko).

### 2. Sync / async split

Two validation paths exist, serving different call sites:

| Function | Module | Callers |
|---|---|---|
| `validate_url_sync` | `url_guard` | Celery workers (sync), stealth_crawler (sync), httpx Crawler (sync) |
| `validate_url_async` | `url_guard` | FastAPI request handlers (async) |
| `validate_url_against_ssrf` | `ssrf_guard` | Legacy — kept for backward compat with worker code |
| `async_validate_ssrf` | `ssrf_guard` | Called by `validate_url_async` to avoid `socket.getaddrinfo` blocking |

The worker process uses sync validation because:
- Celery tasks run in gevent-based greenlets; `asyncio.run()` inside gevent
  is a known deadlock risk (see audit).
- Sync DNS resolution (`socket.getaddrinfo`) in a worker is acceptable — the
  worker's job is to block on I/O.

The API process uses async validation because:
- FastAPI handlers must never block the event loop.
- `loop.getaddrinfo()` delegates DNS to a thread pool, keeping the loop free.

### 3. Exception hierarchy

```
ValueError
├── SSRFError          (ssrf_guard.py) — private/reserved IP resolution
└── URLGuardError       (url_guard.py)  — scheme, port, redirect, body-size policy
```

Both inherit from `ValueError` so existing `except ValueError` catch sites
continue to work without modification.

`UrlNotAllowed` is kept as a backward-compatible alias for `URLGuardError`.

## Alternatives Considered

### Single async path everywhere
- **Rejected:** Would require `asyncio.run()` in Celery workers. Combined with
  gevent monkey-patching, this risks deadlocks. Stage 8 will evaluate switching
  the worker pool to `prefork` and unifying on async.

### DNS cache with short TTL
- **Rejected:** Any cache introduces a TOCTOU window. The attacker's rebinding
  TTL can be tuned to exploit it. Per-hop fresh resolution eliminates the attack
  surface entirely.

### Let upstream proxy handle SSRF
- **Rejected:** Assumes a correctly configured forward proxy with egress rules.
  The proxy pool consists of third-party residential proxies that do NOT filter
  by destination IP. Defense in depth requires application-level validation.

## Consequences

- **Positive:** No known SSRF vector remains in the outbound request path.
- **Positive:** Sync/async split avoids gevent+asyncio deadlock risk in workers.
- **Negative:** Per-hop DNS resolution adds ~5-50ms latency per redirect
  (depending on DNS resolver). Acceptable given the batch/async nature of the
  workload.
- **Negative:** Two parallel implementations (`validate_url_sync` /
  `validate_url_async`) must be kept in sync. The shared `_check_static` and
  `_check_resolved` / `_check_addrs_blocked` helpers reduce this risk.
