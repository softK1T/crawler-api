# Crawler API

FastAPI + arq worker + PostgreSQL + Redis. Engines: HTTPX, curl_cffi,
Playwright Chromium and Camoufox Firefox. Domain policies, proxy candidates,
per-attempt observability and asynchronous batches are included.

## Fresh installation

Requires Git and Docker with a current Compose v2 plugin on Linux x86_64. Host
Python and a manually created .env are not required. Internet access is required
for the first build to install dependencies and both browsers. Native ARM64
builds are not supported by the currently pinned Camoufox artifact.

```bash
git clone https://github.com/softK1T/crawler-api.git
cd crawler-api
docker compose up -d --build --wait
```

Until these changes are merged into master, clone with
`git clone --branch interview-stable https://github.com/softK1T/crawler-api.git`.
The plain clone command above targets the default branch, not an unmerged branch.

The default docker-compose.yml builds entirely from this repository's Dockerfile.
There is no dependency on a developer's existing local application image, home
folder, external override file or preinstalled browser. PostgreSQL and Redis
are pulled from their public images.

### Pinned Camoufox runtime

The upstream releases API became unavailable. Instead of calling camoufox fetch,
the Dockerfile downloads the recovered Linux x86_64 installation from this
repository's release runtime-camoufox-159.0-beta.42-linux-x86_64. The Python
package remains camoufox 0.4.11; browser metadata is 159.0-beta.42.

Archive: camoufox-linux-x86_64.tar.gz (approximately 1.36 GB compressed).
SHA256: `549445fd1a070901cfb7e3f1309e0613f6dd117180d1cd01edd2df61606f9804`.
The build verifies this hardcoded checksum before filtered extraction and checks
the browser metadata and executable. The worker still must pass its real browser
launch self-check; file verification alone is not a successful browser test.
The archive came from the previously working deployment, not an independently
verified upstream signature. Do not overwrite the release asset; use a new
versioned release and reviewed checksum for future changes. No browser download
is performed by this Dockerfile at container startup. A verified ARM64 archive
and platform-specific pin are needed before claiming native ARM64 support.

Startup creates a persistent random database password, waits for PostgreSQL and
Redis, applies Alembic migrations and creates a local administrator key. API and
worker start only after initialization succeeds. PostgreSQL, Redis and credentials
are kept in named volumes. A normal repeat start reuses credentials and data;
it does not mint another administrator key. The first build can take a while.

API: http://127.0.0.1:8000
OpenAPI UI: http://127.0.0.1:8000/docs
Readiness: http://127.0.0.1:8000/readyz
Only the API loopback port is published. PostgreSQL and Redis have no host ports.
For access from another machine use an SSH tunnel or a properly secured HTTPS proxy.

## Obtain the administrator key privately

```bash
API_KEY=$(docker compose exec -T api python scripts/stack_runtime.py key)
curl -fsS http://127.0.0.1:8000/v1/keys -H "X-API-Key: $API_KEY"
```

The key has fetch/archive/admin/keys scopes. Do not echo it, commit it, paste it
into chat or use shell tracing. The initializer never prints it to container logs.
Use POST /v1/keys to issue narrower application keys. A revoked/expired persisted
bootstrap key causes initialization to fail rather than silently undo revocation.
Keep credentials volume and database backups together. This is a convenient
single-host baseline, not a public production security configuration.

## Fetch one page

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/fetch \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","mode":"static","use_proxy":false}'
```

Read the returned job_id with GET /v1/jobs/{job_id}. Results include status_code,
blocked, body_b64, body_bytes and content_sha256. A completed job is not proof
of a successful, unblocked target page; inspect the result and actual content.
The example needs outbound Internet access. Check site terms and robots rules.

## Proxy candidates and batches

The checker is enabled by default. No proxies or paid subscriptions are supplied.
Create a pool with POST /admin/proxy-pools and provider=public-candidates, then
POST /proxy/candidates/import with pool_id and a proxies array of public-IP URLs.
GET /proxy/candidates/pools/{pool_id}/results and /stats show check progress.
Imported proxies start inactive; country is measured, not assumed. Candidates
remain type unknown; do not label them datacenter/residential based on location.

Batch endpoint: POST /batches/ with urls, mode and options. Use
GET /batches/{batch_id}/status and /results to poll. Proxy overrides for batches
belong in options. Candidate selection currently uses options.proxy_type=unknown;
options.use_proxy=true requires a proxy, and options.proxy_country=PL restricts
the country. Bind a domain policy to the intended pool to prevent other pools
from participating. A fresh PL candidate is required for a PL-only test.

WARC/S3, callbacks, Webshare sync and premium proxy tiers are off by default.
Playwright and Camoufox are mandatory and checked on worker startup. Proxy
checker and sync traffic, when enabled, performs real network requests.
The batch admission limit is not a guarantee of per-page pacing or IP rotation.

## Operations

```bash
docker compose ps -a
docker compose logs --tail=100 api worker initialize
curl -fsS http://127.0.0.1:8000/readyz
docker compose stop
docker compose up -d --wait
```

For a deliberate update: back up the database and runtime-secrets volume first,
then git pull --ff-only and docker compose up -d --build --wait. The initializer
applies pending migrations. Do not assume every future migration is reversible.
Docker cache normally reuses dependency/browser layers when inputs are unchanged;
it is not a promise of zero downloads. Dependencies are not fully locked yet.
Never use docker compose down -v on persistent data unless deletion is intended.
It deletes the database, Redis state and credentials. Tests must use disposable
DB/Redis, never this persistent stack.

## Existing installations

This default Compose file is for a fresh, self-contained installation. It does
not automatically adopt an old deployment's volumes, database name, credentials,
project name or port. Do not run it against the existing server stack blindly.
In particular, the previously verified crawler-smoke deployment in an external
folder remains a separate deployment until an explicit data migration is planned.
compose.ubuntu.yaml and UBUNTU.md describe that older/manual setup; they are not
required for a fresh start and must not be combined with the default file.

## Verification and other documentation

Fresh Compose startup is tested by .github/workflows/compose-smoke.yml on a clean
CI runner: build, migrations, browser readiness, authentication, candidate-pool
creation and repeated initialization without key rotation. Check the actual
workflow result for a commit; adding a workflow is not proof it passed. This
smoke test does not validate OLX, supplied proxies, all fetch engines end-to-end,
WARC, load or a production security boundary.

Architecture and operations: docs/runbook.md, docs/decisions/,
docs/PROJECT-HISTORY.md and docs/AI-ASSISTED-DEVELOPMENT.md. Historical test counts
in older documents are not current acceptance results for a new commit.
