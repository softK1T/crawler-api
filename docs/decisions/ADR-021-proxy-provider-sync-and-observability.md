# ADR-021: Proxy Provider Sync and Observability

**Date:** 2026-08-06  
**Status:** Proposed

## Context

`app/worker/tasks/proxy_sync.py` is the arq cron task (`sync_proxies`, registered in `app/worker/arq_worker.py` via `arq_cron(sync_proxies, minute={0, 30}, run_at_startup=True)`) that reconciles the `proxies` table against the Webshare provider list fetched by `app/services/webshare_sync.py`. Direct inspection of the current code found confirmed defects:

1. Raw SQL referenced non-existent `proxies` columns (`username`, `password`, `is_active`).
2. Every new proxy was assigned a new inline `pool_id` instead of one provider pool.
3. Reconciliation matched only on `url`, without provider identity.
4. `WHERE url NOT IN :urls` used unsafe tuple binding in raw `text()`.
5. No unique constraint existed on `proxies.url`, while import code used URL conflict updates.
6. `ProxyManager.report_result()` had no structured per-request proxy outcome log.
7. No durable `proxy_events` history table existed.
8. No proxy events history endpoint existed.

These defects were latent because `sync_proxies()` short-circuited when `WEBSHARE_API_KEY` was unset in CI.

## Decision

### 1) Explicit provider identity on Proxy

- Add `proxies.provider` (`String(64)`, non-null, server default `'webshare'`).
- Add `uq_proxy_provider_url` unique constraint on `(provider, url)`.
- Add `proxies.is_active` (`Boolean`, non-null, server default `true`) for soft deactivation.

### 2) Provider Adapter Pattern

Add `app/services/proxy_providers/`:

- `base.py` with:
  - `RawProxy` dataclass (`url`, `country`, `proxy_type`)
  - `ProxyProvider` abstract base class with `fetch_proxies()`
- `webshare.py` with `WebshareProvider` that reuses existing
  `fetch_webshare_proxies()` and maps provider lines into `RawProxy`.

### 3) ProxySyncService

Add `app/services/proxy_sync_service.py` with `ProxySyncService.sync()`:

- Reconcile proxies per provider using SQLAlchemy 2.x upsert keyed on `(provider, url)`.
- On conflict overwrite only `country`, `proxy_type`, and `updated_at`.
- Compute provider-specific set difference in Python (safe deactivation).
- Keep health/cooldown counters untouched.
- `sync_proxies(ctx)` becomes a thin wrapper creating `WebshareProvider` and invoking service sync.

### 4) One stable pool per provider

- Resolve/create exactly one `ProxyPool` per provider (`{provider}-pool`) and reuse it for all provider proxies.

### 5) Multi-level observability

- In `ProxyManager.report_result()`:
  - emit structured `proxy_result` log (`proxy_id`, `domain`, `engine`, `success`, `reason`, `response_time_ms`)
  - update Redis daily aggregates:
    - key: `proxy:daily:{proxy_id}:{YYYY-MM-DD}`
    - fields: `requests`, `errors`
    - TTL: 172800s (set on first write)
- Add durable transition-only events table `proxy_events` and write events for:
  - `activated` / `deactivated` in sync reconciliation
  - `circuit_open` / `circuit_close` in proxy manager

### 6) Admin proxy-history endpoint

- Add `GET /proxy/proxies/{proxy_id}/events` (admin scope), ordered by newest first with limit (default 50, max 200).

## Alternatives considered

1. Keep raw SQL and patch names only — rejected (still misses pool/provider correctness and safe binding).
2. Provider only on `ProxyPool` — rejected (row-level reconciliation ambiguity remains).
3. Batch metrics only, no request structured logs — rejected (separate concern).
4. Store every request in `proxy_events` — rejected (too noisy; Redis aggregates + transition events are enough).
5. Add event streaming queue — rejected (unjustified and banned stack direction).
6. Split credentials into dedicated columns — rejected (`url` remains credential source of truth).
7. Silent direct fallback when sync/pool fails — rejected (contradicts fail-fast proxy policy).

## Consequences

- New migration `0006_proxy_provider_and_events.py` adds:
  - `proxies.provider`
  - `proxies.is_active`
  - `uq_proxy_provider_url`
  - `proxy_events` table
- Existing rows remain backward-compatible via server defaults.
- Domain policy mappings remain unchanged.
- Added tests:
  - `tests/unit/test_proxy_sync_service.py`
  - `tests/unit/test_proxy_providers.py`
  - `tests/integration/test_proxy_admin_events.py`
