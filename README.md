# Crawler API

A web-page retrieval service with HTTP and browser engines, asynchronous jobs, proxy management, and WARC archives in local S3-compatible storage.

Stack: FastAPI, Redis/arq workers, PostgreSQL for configuration/audit/archive indexes, and RustFS for WARC objects.

## Features

- Four modes: `static` (HTTPX), `stealth` (curl_cffi), `browser` (Playwright/Chromium), and `camoufox` (Camoufox/Firefox).
- Single and batch jobs, result polling, and optional waiting for a single job for up to 30 seconds.
- Domain policies, escalation tiers, proxy pools, health/cooldown tracking, circuit breakers, and background candidate checks.
- Tenants, applications, scoped API keys, key rotation, and usage statistics.
- WARC archival with a persistent write-ahead spool, S3 delivery retries, and index recovery after S3/database failures.
- SSRF checks, rate limiting, readiness checks, and Prometheus metrics. Access to any particular target site is not guaranteed.

## Deployment

Requirements: Linux x86_64, Git, Docker with Compose v2, internet access for the initial build, and disk space for browsers and archives. Native ARM64 is not supported by the current image. No mandatory `.env` file or host Python installation is required.

Fresh installation from `master`:

```bash
git clone --branch master https://github.com/softK1T/crawler-api.git
cd crawler-api
docker compose -p crawler-current -f docker-compose.yml build api
docker compose -p crawler-current -f docker-compose.yml pull postgres redis s3
docker compose -p crawler-current -f docker-compose.yml up -d --no-build --pull never --wait --wait-timeout 300
curl --fail-with-body http://127.0.0.1:8000/readyz
```

Continue only after each preceding command succeeds. Only `api` builds the shared application image. Startup prepares persistent secrets, runs migrations, and initializes the administrator key. `secrets` and `initialize` should exit with code 0; API, worker, PostgreSQL, Redis, and S3 should remain running.

| Component | Address / storage |
|---|---|
| API | `http://127.0.0.1:8000`; change the host port with `API_PORT` |
| Swagger / ReDoc | `/docs` / `/redoc`; request and response schemas at `/openapi.json` |
| S3 API | Docker-internal `http://s3:9000`; bucket `crawler-warc`, region `us-east-1` |
| S3 console | `http://127.0.0.1:9001`; change the host port with `S3_CONSOLE_PORT` |
| Persistent volumes | `postgres-data`, `redis-data`, `runtime-secrets`, `s3-data`, `s3-secrets`, `warc-spool`, prefixed with the Compose project name |

Update an existing canonical stack from its project directory, preserving the Compose project name:

```bash
git pull --ff-only origin master
docker compose -p crawler-current -f docker-compose.yml build api
docker compose -p crawler-current -f docker-compose.yml pull s3
docker compose -p crawler-current -f docker-compose.yml up -d --no-build --pull never --wait --wait-timeout 300
```

Changing the project name may select different volumes. Do not combine `docker-compose.yml` with the legacy standalone `compose.ubuntu.yaml`. Migrating an external deployment requires a separate plan.

If startup fails, inspect the failing service instead of deleting data:

```bash
docker compose -p crawler-current -f docker-compose.yml ps -a
docker compose -p crawler-current -f docker-compose.yml logs --no-color --tail=120 secrets
```

## Usage

Protected endpoints require an `X-API-Key` header. Load the existing bootstrap key without printing it:

```bash
API_KEY=$(docker compose -p crawler-current -f docker-compose.yml exec -T api python scripts/stack_runtime.py key)
curl --fail-with-body http://127.0.0.1:8000/v1/keys -H "X-API-Key: $API_KEY"
```

Do not enable `set -x`, commit credentials, or include them in shared logs. Issue separate least-privilege keys for clients. A newly issued or rotated `raw_key` is returned once. The `admin` scope does not automatically grant other scopes.

Fetch without a proxy:

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/fetch -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' -d '{"url":"https://example.org/","mode":"static","use_proxy":false,"options":{"sync":true}}'
```

Without `options.sync`, submission returns HTTP 202 and a `job_id`. With `sync=true`, the API waits for up to 30 seconds, returning HTTP 200 for a terminal job or 202 otherwise. Continue polling `GET /v1/jobs/{job_id}` when needed. Always inspect `status`: HTTP 200 can contain a failed job. Idempotency uses the JSON field `idempotency_key`, not a request header.

| Canonical Compose setting | Default |
|---|---|
| WARC / local S3 | Enabled |
| Proxy candidate checker | Enabled; candidates must be imported |
| Webshare sync / premium proxy tiers | Disabled; explicitly enable `PROXY_SYNC_ENABLED` / `ENABLE_PREMIUM_PROXY_TIERS`; sync also requires `WEBSHARE_API_KEY` |
| Worker jobs / Chromium pool | `WORKER_MAX_JOBS=2` / one browser |
| Callbacks / tracing | Disabled; enabling requires container environment changes and the relevant credentials/services |

Arbitrary `.env` variables are not automatically forwarded into containers: Compose must explicitly pass them. Candidate geolocation does not establish proxy type or guarantee target-site access.

## Endpoints

Paths below are complete, including trailing slashes. **Key** means any valid API key; resource ownership restrictions still apply. `fetch`, `archive`, `keys`, and `admin` are required scopes. See `/docs` or `/openapi.json` on the running instance for request bodies and complete parameter schemas.

### Fetching, batches, and archives

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/v1/fetch` | `fetch` | Submit a single fetch job |
| GET | `/v1/jobs/{job_id}` | Key, own application | Read job status, result, or error |
| POST | `/batches/` | `fetch` | Submit a batch of jobs |
| GET | `/batches/{batch_id}/status` | `fetch`, own application | Read batch status |
| GET | `/batches/{batch_id}/results` | `fetch`, own application | Read batch results |
| GET | `/v1/archive/` | `archive` | List archive entries; filters: `url`, `from`, `to`, `page`, `per_page` |
| GET | `/v1/archive/{entry_id}` | `archive` | Read archived content as `body_b64`; use an archive entry ID, not a job ID |

Archive routes are registered only when `ENABLE_WARC=true`. The `archive` scope is not an application-isolation guarantee: current archive queries do not filter by application. Grant it only to trusted clients.

### Tenants, applications, projects, and keys

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/v1/tenants` | `admin` | Create a tenant |
| GET | `/v1/tenants` | `admin` | List tenants |
| GET | `/v1/tenants/{tenant_id}` | `admin` | Read a tenant |
| POST | `/projects/` | `admin` | Alternative tenant creation route; not a separate entity |
| GET | `/projects/` | `admin` | List tenants without the `/v1/tenants` pagination wrapper |
| POST | `/v1/applications` | `admin` | Create an application within a tenant |
| GET | `/v1/applications` | Key | List own application; administrators can list all |
| GET | `/v1/applications/{app_id}` | Key | Read own application; administrators can read any |
| PATCH | `/v1/applications/{app_id}` | `admin` | Update name, owner label, or active status |
| POST | `/v1/keys` | `keys` | Issue a key subject to scope and application checks |
| GET | `/v1/keys` | Key | List key metadata for the caller's application |
| DELETE | `/v1/keys/{key_id}` | `keys` | Revoke a key other than the authenticating key |
| POST | `/v1/keys/{key_id}/rotate` | `keys` | Issue a successor with an overlap period |

Cross-application key operations additionally require `admin`. Granting the `keys` scope also requires `admin`; callers cannot issue scopes they do not hold.

### Domain policies and proxy-pool creation

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/admin/domain-policies` | `admin` | Create or update a domain policy |
| GET | `/admin/domain-policies` | `admin` | List policies |
| GET | `/admin/domain-policies/{policy_id}` | `admin` | Read a policy |
| PATCH | `/admin/domain-policies/{policy_id}` | `admin` | Update a policy |
| DELETE | `/admin/domain-policies/{policy_id}` | `admin` | Delete a policy |
| POST | `/admin/domain-policies/{policy_id}/pin-tier` | `admin` | Pin tier 0-6 or unlock it; `tier` and `locked` are query parameters |
| POST | `/admin/proxy-pools` | `admin` | Create a proxy pool |
| POST | `/admin/proxy-pools/{pool_id}/proxies` | `admin` | Add a proxy to a pool |

### Proxies and candidate checks

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/proxy/pools` | Key | List active pools |
| GET | `/proxy/pools/{pool_id}/stats` | Key | Read pool health statistics |
| GET | `/proxy/proxies` | `admin` | List proxies; filters: `pool_id`, `country` |
| GET | `/proxy/proxies/{proxy_id}/events` | `admin` | Read proxy events |
| POST | `/proxy/health` | `admin` | Report a proxy request outcome |
| POST | `/proxy/reset/{proxy_id}` | `admin` | Reset health and cooldown |
| DELETE | `/proxy/circuit-breaker/{domain}` | `admin` | Reset a domain's circuit breaker |
| POST | `/proxy/admin/proxies` | `admin` | Bulk-import structured proxy records |
| POST | `/proxy/candidates/import` | `admin` | Import up to 500 URLs into an active `public-candidates` pool |
| PATCH | `/proxy/candidates/{proxy_id}/enabled` | `admin` | Enable checking/automatic activation or pause a candidate |
| POST | `/proxy/candidates/pools/{pool_id}/recheck` | `admin` | Schedule enabled candidates for rechecking |
| GET | `/proxy/candidates/pools/{pool_id}/stats` | `admin` | Summarize countries and check statuses |
| GET | `/proxy/candidates/pools/{pool_id}/results` | `admin` | List check results with `limit` and `offset` |

### Usage and service endpoints

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/v1/usage/` | Key | Read own application's usage; `from_month` / `to_month` filters |
| GET | `/v1/usage/applications/{application_id}` | `admin` | Read a selected application's usage |
| GET | `/healthz` | Public | API process liveness, not dependency readiness |
| GET | `/readyz` | Public | Check DB, Redis, worker, browsers, and enabled S3 |
| GET | `/metrics` | Public | Prometheus metrics; path configurable via `METRICS_PATH` |
| GET | `/docs` | Public | Swagger UI |
| GET | `/redoc` | Public | ReDoc |
| GET | `/openapi.json` | Public | OpenAPI schema |
| GET | `/docs/oauth2-redirect` | Public | Swagger helper redirect, not a login API |

## Storage and security

- Redis holds temporary job results. Persistent archives require both WARC objects in S3 and their PostgreSQL index. Each new WARC contains one complete response; revisit deduplication is not used.
- S3/database failure fails the job, while an already-written spool entry remains for retry. Archive recovery does not change a failed job's status. The spool is capped at 5 GiB; full storage rejects new entries instead of evicting existing ones.
- Do not run `down -v`, `prune`, or tests against deployment databases/Redis. Volumes are not protection against disk failure: keep consistent off-host backups of the database, S3, secrets, and spool.
- API and S3 console bind to loopback; the S3 API is Docker-internal. External access requires TLS/reverse proxy, restricted service endpoints, and appropriate CORS configuration. Keep SSRF protection enabled for untrusted URLs.
- Local S3 is dedicated to this project and currently shares one administrative credential pair with the application. Do not give it to other applications. The loopback stack is not a completed production or multi-tenant security audit.

Archival/recovery details: [Local S3](docs/LOCAL-S3.md). Additional material: [Documentation](docs/README.md).

## Project language

All project documentation, code comments, user-facing messages, and commit messages must be written in English.
