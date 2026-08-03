# ADR-017: Proxy Selection and Rotation Policy

**Date:** 2026-08-03
**Status:** Accepted

## Context

When `proxy_id=null` appears in a fetch result there are four possible
causes: empty proxy pool, no tenant binding (pool_id), all proxies on
cooldown (health threshold), or the `use_proxy` flag was lost between
the API and worker.

The current implementation has several gaps:

1. **No `use_proxy` on the request path.** `JobCreate` has no proxy
   control field. The ARQ payload carries `options` but the worker
   doesn't extract proxy overrides from it.
2. **Silent fallback.** `ProxyManager.get_proxy()` returns `None` when
   no proxy is eligible. `fetch_with_retry` passes `None` to the fetcher
   and the request goes out direct — the caller never knows.
3. **No rotation.** When a proxy returns a block page, the retry loop
   picks the same proxy again (sticky session is pinned by `job_id`).
4. **`DomainPolicy` has no proxy controls.** There's no per-domain
   `use_proxy` or `proxy_country` field.

## Decision

### Three-level proxy resolution

```python
effective_use_proxy = (
    request.use_proxy  # explicit request override (bool | None)
    if request.use_proxy is not None
    else domain_policy.use_proxy  # DomainPolicy row (bool, default True)
)

effective_country = (
    request.proxy_country  # explicit request override
    if request.proxy_country is not None
    else domain_policy.proxy_country  # DomainPolicy row (str | None)
)
```

This uses `bool | None` rather than `bool = False` — otherwise it's
impossible to distinguish an explicit `use_proxy=False` from a missing
field.

### Request schema

`JobCreate` gains two optional fields:

```python
use_proxy: bool | None = None
proxy_country: str | None = Field(None, min_length=2, max_length=2)
```

The API endpoint merges them into `options` before enqueuing so they
reach the worker unchanged.

### DomainPolicy model

Two new columns:

- `use_proxy` — `Boolean`, default `True`, non-nullable.
- `proxy_country` — `String(2)`, nullable, ISO 3166-1 alpha-2.

Alembic migration `0003` adds both.

### Fail-fast selection

When `effective_use_proxy=True` and no healthy proxy is available:

```python
if proxy is None:
    if failed_proxy_ids:
        raise ProxyPoolExhaustedError(...)
    raise ProxyPoolUnavailableError(...)
```

These are hard failures — retries stop immediately. The worker stores
the error and the caller sees a 502 with the specific error code.

### Proxy rotation on block

The retry loop tracks `failed_proxy_ids: set[UUID]`. When a proxy
returns a block:

1. The proxy is reported as failed (health decay, cooldown).
2. Its ID is added to `failed_proxy_ids`.
3. On the next attempt, `get_proxy(exclude_ids=failed_proxy_ids)`
   skips already-tried proxies.
4. When all eligible proxies are exhausted: `ProxyPoolExhaustedError`.

Sticky sessions are cleared after the first attempt (`sticky_key=None`
on retries) so rotation can pick a different proxy.

### `ProxyManager.get_proxy()` signature

Two new keyword-only parameters:

- `exclude_ids: set[UUID] | None` — proxies to skip.
- `country: str | None` — ISO 3166-1 alpha-2 filter.

Sticky sessions are also checked against `exclude_ids` so a blocked
sticky proxy is not re-pinned.

### Health metric emission

`proxy_manager.report_result()` now emits `crawler_proxy_health_score`
after every health mutation, so dashboards reflect the latest state
without waiting for the next poll cycle.

## Alternatives considered

1. **Keep `return None` and let callers check.** Rejected: silent
   fallback to direct connection violates the principle of least
   surprise. The caller asked for a proxy — they should get a proxy
   or a clear error.

2. **Rotation via sticky session invalidation only.** Rejected:
   sticky invalidation depends on Redis; with Redis down the worker
   would retry the same proxy indefinitely. In-process `exclude_ids`
   is more robust.

3. **Add `tenant_id` as a proxy selection dimension.** Deferred:
   the codebase uses `pool_id` (via `DomainPolicy`) for proxy
   partitioning. Tenant-aware selection can be built on top of this
   without a schema change.

## Consequences

- `POST /v1/fetch` gains `use_proxy` and `proxy_country` fields
  (both optional, backward-compatible).
- Workers will raise hard errors when proxy is requested but
  unavailable — callers must handle 502 responses.
- Proxy rotation increases pool utilization under blocking: each
  retry tries a different proxy.
- DomainPolicy migration `0003` must be applied.
