# ADR-004: Authentication Dependency Chain

**Status:** Accepted
**Date:** 2026-07-27
**Stage:** Stage 3 — Authentication

## Context

Stage 1 introduced argon2id hashing and a flat-list `get_api_key` dependency.
Stage 2 defined the data model: `ApiKey` rows in PostgreSQL with prefix,
hashed_key, scopes, mode, and lifecycle fields (revoked_at, expires_at).
Stage 3 must bridge the two — every protected request must resolve the caller's
`ApiKey` row from the database and enforce scopes.

## Decision

### 1. Primary auth path: `resolve_api_key`

```
X-API-Key header
  → extract prefix (first 8 chars)
    → SELECT api_keys WHERE prefix = :p AND is_active = TRUE
      → no row → 401 AuthenticationError
      → row.revoked_at IS NOT NULL → KeyRevokedError
      → row.expires_at < now() → KeyExpiredError
      → argon2.verify(raw_key, row.hashed_key) fails → 401 AuthenticationError
      → fire-and-forget update_last_used (non-blocking)
      → return ApiKey ORM row
```

The legacy `get_api_key` flat-list dependency from Stage 1 is kept for
backward compatibility with existing tests but must not be used by any new
endpoint (Stage 3+).

### 2. Scope enforcement: `require_scope(scope)`

A dependency factory that returns a FastAPI dependency. It calls
`resolve_api_key` first, then checks `scope in api_key.scopes` via Python's
`in` operator on the loaded ARRAY column. Four scopes are defined:

| Scope | Constant | Grants |
|---|---|---|
| `fetch` | `SCOPE_FETCH` | Submit crawl jobs |
| `archive` | `SCOPE_ARCHIVE` | Read WARC archive |
| `admin` | `SCOPE_ADMIN` | Manage domain policies, proxy pools, tenants |
| `keys` | `SCOPE_KEYS` | Create/revoke API keys |

Scope names use exact string match. No wildcards, no hierarchy (e.g., `admin`
does not imply `fetch`). If a scope is not in `ALL_SCOPES`, the dependency
factory raises `ValueError` at registration time (bug detection).

### 3. `update_last_used` is fire-and-forget

The `last_used_at` timestamp is updated via `asyncio.create_task()`, not
awaited. Rationale:
- **Latency:** An UPDATE + COMMIT adds ~2-5ms to every authenticated request.
  For a crawl API where auth is on the hot path, this is unacceptable.
- **Correctness:** `last_used_at` is informational only (dashboard display,
  key rotation decisions). A one-event-loop-tick lag is tolerable.
- **Safety:** The wrapper catches all exceptions silently. If the task runs
  after the request's DB session is closed, the error is logged and ignored.

### 4. Key lifecycle

- **Creation** (`POST /v1/keys`): `generate_api_key()` produces `(raw, hashed)`.
  The raw key is returned exactly once. Prefix collisions retry once.
- **Revocation** (`DELETE /v1/keys/{key_id}`): Sets `revoked_at = now()` and
  `is_active = False`. Hard delete of the row is not performed — the hashed_key
  remains for audit trail.
- **Expiry** (`expires_at`): Checked on every request. No background job sweeps
  expired keys; `is_active` stays `TRUE` but the expiry check in
  `resolve_api_key` rejects them.

## Alternatives Considered

### Middleware-based auth instead of dependency injection
- **Rejected:** Middleware runs before route matching and cannot express
  per-endpoint scope requirements. FastAPI's `Depends` pattern allows
  composable auth (`require_scope(SCOPE_KEYS)` vs `require_scope(SCOPE_ADMIN)`).

### Background task queue for update_last_used
- **Rejected:** Adds complexity (Celery task per request) for a non-critical
  field. `asyncio.create_task` is sufficient.

### Hierarchical scopes (admin implies all)
- **Rejected:** Explicit scoping is simpler to reason about and audit. A
  compromised `admin` key should not silently gain `fetch` capability — the
  operator must explicitly grant both.

## Consequences

- **Positive:** Auth is decoupled from endpoint logic. Adding a new endpoint
  with scope enforcement is one line: `Depends(require_scope(SCOPE_FETCH))`.
- **Positive:** `last_used_at` is eventually consistent without blocking
  responses.
- **Negative:** `resolve_api_key` makes one DB query per request. With
  connection pooling and a `prefix` index, this is ~1ms. A future Redis cache
  (Stage 8) could reduce this further.
- **Negative:** The `application` relationship on `ApiKey` is not eagerly
  loaded (no `selectinload`) because the ORM relationship was deferred to
  avoid touching `app/models/`. The `application_id` column is directly
  accessible on the row.
