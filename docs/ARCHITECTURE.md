# Architecture

[Documentation](README.md) · [API](API.md) · [Configuration](CONFIGURATION.md)

## Components and state

| Component | Responsibility | Persistent state |
|---|---|---|
| FastAPI | Authenticate keys, admit requests, enqueue jobs, serve results and archives | PostgreSQL-backed identity/configuration |
| Redis/arq | Queue, admission limits, job/batch state, worker heartbeat | Redis volume/AOF; results also have TTLs |
| Worker | Resolve policy, run attempts, archive captures, record usage | PostgreSQL attempt history and WARC journal |
| PostgreSQL | Tenants, applications, keys, global policies/pools, usage, archive index | `postgres-data` |
| RustFS | Local S3-compatible WARC object storage | `s3-data` plus `s3-secrets` |

## Request lifecycle

1. The API verifies the key and required scope, resolves policy, and checks admission limits.
2. A job is queued on `arq:crawler`; the client receives a `job_id`.
3. The worker selects an engine and routing policy, executes transport attempts, and records independent attempt audit rows.
4. Non-blocked captures are serialized as complete WARC response objects. Bytes and index metadata are journalled before upload.
5. S3 upload and PostgreSQL index commit allow journal removal. Failures leave entries for replay; a failed job remains failed even if its archive is recovered later.
6. The client polls temporary Redis job results, or uses the separate archive-entry API for persistent captures.

## Startup and readiness

The secrets service prepares database/S3 credentials and storage permissions. PostgreSQL, Redis, and S3 become healthy; initialization applies migrations and prepares the administrator key; API and worker then start. Browser self-checks feed a short-lived worker heartbeat. `/readyz` checks dependencies and enabled S3 with an authenticated bucket request; `/healthz` only checks API process liveness.

## Routing and isolation

The four modes select HTTPX, curl_cffi, Playwright, or Camoufox, subject to domain policy and escalation rules. Policy engine settings can override request mode. Proxy pools and domain policies are global. Browser requests use isolated contexts; this is not network-level isolation.

Job/batch reads check application ownership. Archive queries currently do not filter by application. Do not infer tenant isolation from the existence of tenant/application records. See [security boundaries](SECURITY.md).

The service retrieves pages; shop-specific extraction, scheduling, and site-login orchestration are not provided by the tenant-compatible `/projects/` routes. [Design decisions](DESIGN.md) gives the reasons behind the current design.
