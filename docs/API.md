# API reference

[Documentation](README.md) · [User guide](USER_GUIDE.md) · [Security](SECURITY.md)

Live /openapi.json and /docs define the deployed revision. There is no universal
/v1 prefix. Send X-API-Key and JSON Content-Type where appropriate. Scopes are
independent: admin does not implicitly grant fetch or keys. Slashes match routes.

## Identity and keys

| Method | Route | Access / purpose |
|---|---|---|
| POST, GET | /v1/tenants | admin; create/list |
| GET | /v1/tenants/{tenant_id} | admin |
| POST | /v1/applications | admin; tenant_id/name |
| GET | /v1/applications | Valid key; own app, or all for admin |
| GET | /v1/applications/{app_id} | Own app or admin |
| PATCH | /v1/applications/{app_id} | admin; name/owner_label/is_active |
| POST | /v1/keys | keys; held scopes only, cross-app/granting keys needs admin |
| GET | /v1/keys | Caller application's keys |
| DELETE | /v1/keys/{key_id} | keys plus ownership/admin; not self-revocation |
| POST | /v1/keys/{key_id}/rotate | keys plus ownership/privilege checks |
| POST, GET | /projects/ | admin; compatibility tenant create/list |

Issue-key body: application_id, scopes, mode (live by default), optional expires_at.
raw_key is returned once on issue/rotation. Rotation preserves scopes/expiry and
bounds old-key overlap; it cannot reactivate expired/revoked keys. test is not a
sandbox. Application deactivation is not a substitute for key revocation.

## Fetch and results

POST /v1/fetch requires fetch scope. Its contract is JobCreate, not legacy
CrawlRequest. Required url; optional fields:

| Field | Values / meaning |
|---|---|
| mode | static / stealth / browser / camoufox; default static |
| use_proxy | Optional boolean; omitted leaves routing resolution to policy/ladder |
| proxy_country / proxy_city | Two-character country / exact city filter |
| proxy_type | Top-level residential / datacenter / isp |
| session_key | Optional session identifier |
| idempotency_key | Application-scoped string, max 128 chars |
| callback_url | Rejected when callbacks disabled |
| options | Worker options; sync=true enables a bounded synchronous wait |

```json
{"url":"https://example.com/","mode":"static","use_proxy":false,"idempotency_key":"demo-001"}
```

Normally returns 202 with job_id/status/created_at/idempotency_key. options.sync=true
waits about 30 seconds, returning full completion if available or 202 otherwise.
Replay can return 200. Use the implemented body idempotency field; do not assume
an arbitrary header substitutes for it or that enqueueing is exactly-once atomic.
Unknown candidates currently use options.proxy_type=unknown, not top-level type.
Do not infer support for headers/extract/wait_for/timeout/delay or a pool selector
from unused schemas: inspect actual enqueue/worker call sites.

GET /v1/jobs/{job_id}: valid key plus application ownership; foreign/missing IDs 404.
pending → running → completed/failed. A failed asynchronous job can be returned by
HTTP 200; inspect status/error rather than expecting polling HTTP 502.

Result fields: url/status_code/headers, body_b64/encoding, body_is_compressed,
body_bytes/content_sha256, original_content_encoding, engine/tier_used/retries_used,
proxy_id/proxy_country, blocked/block_reason, elapsed_ms/trace_id, api_version=2.
API bytes are normalized/uncompressed then base64 encoded. Verify length/hash and
actual content. completed, HTTP 200 or blocked=false alone is not page success.

## Batches

POST /batches/: fetch scope; urls, mode, optional callback_url/options. Schema cap
100 URLs, default configured cap 20. Proxy overrides belong inside options.
Returns batch_id/job_ids/total_count. GET /batches/{batch_id}/status and /results
require fetch scope and application ownership. Status completed counts terminal
jobs including failures; results successful counts completed jobs, including
potential blocked/non-2xx results. Partial results are not a final verdict.
Each URL charges key/application admission, while domain admission is once per
unique domain. This is not per-page pacing or atomic all-or-nothing enqueueing.

## Domain policy and proxies

All /admin operations need admin. POST /admin/domain-policies upserts a normalized
domain; GET lists with domain substring/is_active filters. GET/PATCH/DELETE
/admin/domain-policies/{policy_id} manage a row. POST .../{policy_id}/pin-tier uses
tier/locked query parameters. Read live schemas for engine, pool, rates, retry,
headers and escalation fields. PATCH ignores None; POST defaults can overwrite
configuration. Snapshot policy before testing. A robots flag is not proof of
complete enforcement on every transport.

| Method | Route | Access / purpose |
|---|---|---|
| POST | /admin/proxy-pools | admin; name/provider, including public-candidates |
| POST | /admin/proxy-pools/{pool_id}/proxies | admin; direct import, body also requires pool_id |
| GET | /proxy/pools | Valid key; active pools |
| GET | /proxy/pools/{pool_id}/stats | Valid key; aggregate health |
| GET | /proxy/proxies | admin; pool_id/country filters |
| GET | /proxy/proxies/{proxy_id}/events | admin; history |
| POST | /proxy/health | admin; report outcome |
| POST | /proxy/reset/{proxy_id} | admin; reset health/cooldown |
| DELETE | /proxy/circuit-breaker/{domain} | admin; explicit reset |
| POST | /proxy/admin/proxies | admin; legacy bulk import, not quarantine |

Legacy bulk requires tenant_id/proxy objects and creates a provider pool; it does
not provide candidate checking. Generic pool stats do not prove fresh eligibility.
Candidate import, pause, recheck and results: [proxy guide](PROXY_CANDIDATES.md).

## Usage and optional archives

GET /v1/usage/: own application; GET /v1/usage/applications/{application_id}: admin.
from_month/to_month filter periods. Accounting is a service estimate, not a provider
invoice. With WARC enabled, archive scope allows GET /v1/archive/ (url/from/to/page/
per_page) and GET /v1/archive/{entry_id}. Listing is metadata; content is base64.
These routes are absent when disabled. Archive queries currently lack application
filtering; review/fix ownership before untrusted multi-tenant exposure.

## Health and errors

/healthz, /readyz and /metrics have no scoped-key dependency; protect diagnostics.
401: invalid/missing/expired key; 403: privilege; 404: missing/foreign resource;
409: conflict/rotation state; 422: schema/feature/size; 429: admission, inspect
Retry-After; 503: readiness/optional archive. CrawlerAPIError uses error/detail;
HTTPException and validation errors can have different detail shapes. Target HTTP
failures inside jobs and API submission errors are separate layers.
