# API reference

[Documentation](README.md) · [Usage examples](USER_GUIDE.md) · [Security](SECURITY.md)

## Conventions

- Base URL: `http://127.0.0.1:8000`. Paths below are complete: there is no universal `/v1` prefix.
- Send `X-API-Key` for protected routes and `Content-Type: application/json` for JSON bodies.
- `Key` means any valid database-backed API key. Scopes are independent: `admin` does not imply `fetch`, `archive`, or `keys`.
- `{name}` is a path parameter, usually a UUID; job/batch IDs are strings. Preserve trailing slashes shown below.
- Tables describe the current route handlers. `/openapi.json` and `/docs` on your deployed revision provide complete schemas. Example IDs are placeholders, not live resources.

## 1. Fetch jobs

| Method | Path | Access | Success | Request / response |
|---|---|---|---|---|
| POST | `/v1/fetch` | `fetch` | 202 or 200 | Submit `JobCreate`; returns job identity, or a terminal result with synchronous waiting |
| GET | `/v1/jobs/{job_id}` | Key + application ownership | 200 | Returns status, result/error and timestamps; missing/foreign jobs return 404 |

### Submit a job

| JSON field | Required / default | Meaning |
|---|---|---|
| `url` | Required | HTTP/HTTPS target URL |
| `mode` | `static` | `static`, `stealth`, `browser`, or `camoufox` |
| `use_proxy` | Unset | Explicit routing override; unset leaves resolution to policy/ladder |
| `proxy_country` | Unset | Two-character country code |
| `proxy_city` | Unset | City filter, 1-128 characters |
| `proxy_type` | Unset | Top-level values: `residential`, `datacenter`, `isp` |
| `session_key` | Unset | Optional session identifier, 1-128 characters |
| `idempotency_key` | Unset | Application-scoped submission key, at most 128 characters |
| `callback_url` | Unset | Rejected while callbacks are disabled |
| `options` | `{}` | `sync: true` enables a wait of up to 30 seconds |

```json
{
  "url": "https://example.org/",
  "mode": "static",
  "use_proxy": false,
  "idempotency_key": "example-request-001",
  "options": {"sync": true}
}
```

| Outcome | What to do |
|---|---|
| HTTP 202 with `job_id`, `status`, `created_at`, `idempotency_key` | Poll the returned job ID; acceptance is not target success |
| HTTP 200 after synchronous waiting | Inspect terminal `status`, including `failed` |
| HTTP 202 after the wait expires | Continue polling; do not assume the job was cancelled |
| HTTP 200 with `Idempotency-Key-Status: replayed` | Existing application-scoped submission was reused |

Idempotency is implemented through the JSON field, not an arbitrary header. It is not an atomic exactly-once guarantee across enqueueing and side effects. Domain policy can override the requested engine. Do not infer support for arbitrary `headers`, `extract`, timeout/delay fields, or a request pool selector from legacy schemas.

### Read a result

Lifecycle: `pending` -> `running` -> `completed` or `failed`. The polling endpoint can return HTTP 200 for either terminal outcome.

| Result fields | Interpretation |
|---|---|
| `status_code`, `headers`, `blocked`, `block_reason` | Target response and block detection; a completed job can still contain an error/challenge |
| `body_b64`, `body_bytes`, `content_sha256` | Normalized API body and integrity metadata; decode base64, then check length/hash |
| `original_content_encoding`, `encoding`, `body_is_compressed` | Body representation metadata; do not blindly decompress twice |
| `engine`, `tier_used`, `retries_used` | Actual execution path, not merely requested mode |
| `proxy_id`, `proxy_country`, `elapsed_ms`, `trace_id` | Routing, timing, and correlation |

Inspect actual content as well as HTTP/block fields. Redis job results expire; persistent archive entries use separate identifiers.

## 2. Batches

| Method | Path | Access | Success | Request / response |
|---|---|---|---|---|
| POST | `/batches/` | `fetch` | 202 | `urls`, optional `mode`, `callback_url`, `options`; returns `batch_id`, `job_ids`, `total_count` |
| GET | `/batches/{batch_id}/status` | `fetch` + ownership | 200 | Aggregated job progress; missing/foreign batch returns 404 |
| GET | `/batches/{batch_id}/results` | `fetch` + ownership | 200 | Available job results; inspect each outcome |

```json
{
  "urls": ["https://example.org/"],
  "mode": "static",
  "options": {"use_proxy": false}
}
```

`urls` must be nonempty. The schema caps the list at 100; default configured admission caps it at 20. Proxy overrides belong inside `options`, unlike the top-level single-job fields. Callbacks must be enabled before supplying a callback URL. Batch completion/success counts do not guarantee unblocked 2xx content. Admission charges URLs at the key/application level; domain admission is not per-page pacing. Partial enqueueing/results are not an all-or-nothing transaction.

## 3. Archive entries

| Method | Path | Access | Success | Request / response |
|---|---|---|---|---|
| GET | `/v1/archive/` | `archive` | 200 | Metadata list, newest first; filters below |
| GET | `/v1/archive/{entry_id}` | `archive` | 200 | Extracted body in `body_b64`, content metadata, object filename and digest |

| Query parameter | Meaning |
|---|---|
| `url` | Exact captured URL |
| `from`, `to` | Capture datetime bounds |
| `page` | Page number, default 1 |
| `per_page` | Page size, default 20, maximum 100 |

Use the listed archive entry's `id`, not a job ID. Missing index entries return 404; object-storage/read failures return 503. Routes are absent when WARC is disabled. Current queries do not filter by application: `archive` is a trusted-operator capability, not a tenant-isolation boundary. New WARC objects hold complete responses; existing revisit entries are resolved through the original index record when available.

## 4. Tenants, applications, and compatibility projects

| Method | Path | Access | Success | Request / response |
|---|---|---|---|---|
| POST | `/v1/tenants` | `admin` | 201 | Body `name` (2-128 characters); returns tenant |
| GET | `/v1/tenants` | `admin` | 200 | `limit` (default 50, max 200), `offset`; returns `items`, `total` |
| GET | `/v1/tenants/{tenant_id}` | `admin` | 200 | Tenant record or 404 |
| POST | `/projects/` | `admin` | 201 | Same tenant creation concept; body `name` |
| GET | `/projects/` | `admin` | 200 | Tenant array, not the paginated `/v1/tenants` wrapper |
| POST | `/v1/applications` | `admin` | 201 | Body `tenant_id`, `name` (2-128); tenant must be active |
| GET | `/v1/applications` | Key | 200 | `limit`, `offset`; own application, all for admin; returns `items`, `total` |
| GET | `/v1/applications/{app_id}` | Key + ownership/admin | 200 | Application record; foreign non-admin access returns 404 |
| PATCH | `/v1/applications/{app_id}` | `admin` | 200 | Body: optional `name`, `owner_label`, `is_active`; returns updated record |

`/projects/` does not create a crawler configuration, login flow, or independent proxy universe. Domain policies/pools remain global. Deactivating an application is not a substitute for key revocation.

## 5. API keys

| Method | Path | Access | Success | Request / response |
|---|---|---|---|---|
| POST | `/v1/keys` | `keys` + grant checks | 201 | Body below; response includes one-time `raw_key` |
| GET | `/v1/keys` | Key | 200 | Key metadata for the caller's application; no raw credentials |
| DELETE | `/v1/keys/{key_id}` | `keys` + ownership/admin | 200 | Optional body `reason`; revoked key metadata; cannot revoke the authenticating key |
| POST | `/v1/keys/{key_id}/rotate` | `keys` + privilege checks | 201 | No body required; successor metadata and one-time `raw_key` |

```json
{
  "application_id": "00000000-0000-0000-0000-000000000001",
  "scopes": ["fetch"],
  "mode": "live"
}
```

Replace the example UUID with an existing active application's ID. Optional `expires_at` is a datetime. Allowed scopes are `fetch`, `archive`, `keys`, `admin`; callers may grant only scopes they hold. Granting `keys` or issuing/revoking/rotating across applications additionally requires `admin`. Rotation also checks target privileges. Old-key overlap is controlled by `KEY_ROTATION_OVERLAP_HOURS`; store the returned successor privately. `mode=test` does not prevent real network activity.

## 6. Domain policies

All routes in this section require `admin`.

| Method | Path | Success | Request / response |
|---|---|---|---|
| POST | `/admin/domain-policies` | 201 | Create/update a normalized `domain`; returns policy |
| GET | `/admin/domain-policies` | 200 | Filters: domain substring, `is_active`; returns policies |
| GET | `/admin/domain-policies/{policy_id}` | 200 | Policy record or 404 |
| PATCH | `/admin/domain-policies/{policy_id}` | 200 | Update supported non-null fields; returns policy |
| DELETE | `/admin/domain-policies/{policy_id}` | 204 | Delete policy; no response body |
| POST | `/admin/domain-policies/{policy_id}/pin-tier` | 200 | Query `tier` (0-6, optional), `locked` (default true); returns policy |

Create requires `domain`. Useful writable fields:

| Field | Values / limits |
|---|---|
| `engine` | `httpx`, `curl_cffi`, `playwright`, `camoufox` |
| `proxy_pool_id` | Pool UUID |
| `rate_limit_rps` | 0.1-100; default 1.0 |
| `max_retries` | 0-10; default 3; per-tier behavior is handled by retry orchestration |
| `min_delay_ms`, `max_delay_ms` | 0-60000; defaults 500/2000; do not assume universal transport pacing |
| `header_profile`, `sticky_session`, `respect_robots` | Header configuration/flags; a flag does not certify enforcement on every engine |
| `proxy_type` | `datacenter`, `residential`, `mobile`, `isp` |
| `escalation_tier`, `tier_locked` | Tier 0-6 and automatic-adjustment lock |
| `max_escalation_attempts` | 1-50; default 12 |

PATCH additionally accepts `is_active`. Response fields are not automatically writable fields. PATCH ignores null values, so do not assume null clears a relationship. POST is an upsert whose defaults can overwrite an existing policy: snapshot before testing. To unlock a tier, pass `locked=false` as a query parameter, not JSON.

## 7. Proxy pools and direct imports

| Method | Path | Access | Success | Request / response |
|---|---|---|---|---|
| POST | `/admin/proxy-pools` | `admin` | 201 | `name`, `provider`; returns pool |
| POST | `/admin/proxy-pools/{pool_id}/proxies` | `admin` | 201 | `pool_id`, credentialed `url`, optional `country`, `proxy_type`; returns safe proxy metadata |
| GET | `/proxy/pools` | Key | 200 | Active pools |
| GET | `/proxy/pools/{pool_id}/stats` | Key | 200 | Aggregate health/cooldown statistics |
| GET | `/proxy/proxies` | `admin` | 200 | Filters: `pool_id`, `country`; safe proxy metadata, not credentialed URLs |
| GET | `/proxy/proxies/{proxy_id}/events` | `admin` | 200 | Event history; `limit` defaults to 50, max 200 |
| POST | `/proxy/health` | `admin` | 200 | Body: `proxy_id`, `domain`, `success`, optional `reason`; records outcome |
| POST | `/proxy/reset/{proxy_id}` | `admin` | 200 | Reset health/cooldown; no body required |
| DELETE | `/proxy/circuit-breaker/{domain}` | `admin` | 200 | Reset normalized domain's breaker |
| POST | `/proxy/admin/proxies` | `admin` | 201 | Legacy structured bulk import; returns `imported` |

Pool providers: `webshare`, `custom`, `residential`, `public-candidates`. Direct add requires `pool_id` in the JSON schema as well as the path; use the same ID. Its URL format is `http://user:pass@host:port` (HTTPS also accepted). Top-level direct-import `proxy_type` values are `residential`, `datacenter`, `isp`.

Legacy bulk body requires `tenant_id` and `proxies`, where each item has `host`, `port`, `username`, `password`, `country`, and optional `proxy_type`. Do not treat `tenant_id` as proxy-pool isolation; the handler creates a provider pool. This path is not candidate quarantine. Health `reason` values are `http_error`, `timeout`, `blocked`, `captcha`, or null. Generic pool health does not prove fresh candidate eligibility.

## 8. Proxy candidates

All routes require `admin`. Import/recheck/statistics/results operate on an active pool with provider `public-candidates`.

| Method | Path | Success | Request / response |
|---|---|---|---|
| POST | `/proxy/candidates/import` | 201 | `pool_id`, `proxies` (1-500 public-IP proxy URL strings); returns created/duplicate counts |
| PATCH | `/proxy/candidates/{proxy_id}/enabled` | 200 | Body `enabled`; returns candidate ID and new check status |
| POST | `/proxy/candidates/pools/{pool_id}/recheck` | 202 | Schedule permitted managed candidates; returns `queued` |
| GET | `/proxy/candidates/pools/{pool_id}/stats` | 200 | Grouped country/check-status totals and fresh-active counts |
| GET | `/proxy/candidates/pools/{pool_id}/results` | 200 | `limit` (default 100, max 500), `offset`; safe check-result fields |

Import creates inactive pending candidates. Enabling permits checks and future activation; it does not immediately mark the proxy healthy. Rechecking deactivates candidates until they pass again. A country result is not a commercial proxy-type certificate. See the [acceptance guide](PROXY_CANDIDATES.md) before routing real traffic through them.

## 9. Usage and service routes

| Method | Path | Access | Success | Request / response |
|---|---|---|---|---|
| GET | `/v1/usage/` | Key | 200 | Own application usage; optional `from_month`, `to_month` dates |
| GET | `/v1/usage/applications/{application_id}` | `admin` | 200 | Selected application's usage with the same date filters |
| GET | `/healthz` | Public | 200 | API process liveness |
| GET | `/readyz` | Public | 200 or 503 | Dependency/browser checks and `ready` / `not_ready` |
| GET | `/metrics` | Public | 200 | Prometheus output of the API process; path configurable through `METRICS_PATH` |
| GET | `/docs` | Public | 200 | Swagger UI |
| GET | `/redoc` | Public | 200 | ReDoc |
| GET | `/openapi.json` | Public | 200 | Deployed OpenAPI schema |
| GET | `/docs/oauth2-redirect` | Public | 200 | Swagger helper redirect, not an authentication API |

Each process exports only what it counts itself. `/metrics` on the API has API latency, rate-limit denials and queue depth. Fetch attempts, blocks, escalation tiers, proxy health and cost, and WARC bytes are counted in the worker, which serves them on its own port: `http://127.0.0.1:9464/metrics` in the Compose stack (`WORKER_METRICS_PORT`). Scrape both.

Usage responses include period records, total requests/bytes, and an estimated service cost, not a provider invoice. Protect public service/docs routes when exposing the application beyond loopback.

## Error handling

| HTTP status | Typical API meaning | Client action |
|---|---|---|
| 401 | Missing/invalid/expired credential | Supply a valid key; inspect expiry/revocation |
| 403 | Required privilege missing | Correct scopes/ownership; do not retry unchanged |
| 404 | Missing or deliberately hidden foreign resource | Check ID and application identity |
| 409 | Conflicting name/state/pool | Inspect current resource before changing it |
| 422 | Invalid schema, disabled feature, configured limit | Correct the request |
| 429 | Admission limit | Inspect `Retry-After` when present and back off |
| 503 | Dependency readiness or archive read failure | Inspect dependency state; distinguish retryable infrastructure failure from missing index entries |

`CrawlerAPIError` returns `error`/`detail`; FastAPI validation and `HTTPException` responses can have different `detail` shapes. Do not assume one universal error envelope. Submission errors, polling HTTP status, task status, and target HTTP status are separate layers. In particular, HTTP 200 from polling is not a successful page fetch.

Contract sources: `app/api/v1/endpoints/`, `app/schemas/`, `app/api/v1/dependencies.py`, and the deployed OpenAPI schema. Historical audits and unused request models are not the current API contract.

Per-request headers, cookies, User-Agent and the domain-policy switches: see [REQUEST_OPTIONS.md](REQUEST_OPTIONS.md).
