# Operator runbook

This runbook applies to the repository's default docker-compose.yml, not to a
previous deployment in an external Compose directory. Check the project and
file before every operation. Commands below run from the repository root.
Never point pytest fixtures at a running deployment: they drop tables and flush Redis.

## Fresh startup

Linux x86_64, Git, Docker and a current Compose v2 plugin are required.
Until the release PR is merged, select interview-stable explicitly.

```bash
git clone --branch interview-stable https://github.com/softK1T/crawler-api.git
cd crawler-api
docker compose up -d --build --wait
curl -fsS http://127.0.0.1:8000/readyz
```

No mandatory .env, host Python, separate migration command or prebuilt local
application image is needed. The first build downloads the pinned browser
archive from this project's release; SHA256 is checked before extraction.
PostgreSQL, Redis, database credentials and the bootstrap key use named volumes.
Never use down -v unless intentionally deleting all of that state.

## Credentials and API

```bash
API_KEY=$(docker compose exec -T api python scripts/stack_runtime.py key)
```

Do not echo the key, enable set -x or commit credentials. This key has admin,
keys, fetch and archive scopes; issue narrower keys through POST /v1/keys.
The persisted bootstrap key is reused on normal initialization. An invalid or
revoked key is not silently replaced: recovery is an operator action.
API_KEYS_RAW is not a substitute for the DB-backed keys used by these endpoints.

Use live OpenAPI at /docs or /openapi.json for schemas. Actual routes include:

- POST /v1/tenants, /v1/applications, /v1/keys;
- POST /v1/fetch; GET /v1/jobs/{job_id};
- POST /batches/; GET /batches/{batch_id}/status and /results;
- POST /admin/domain-policies; GET /admin/domain-policies;
- POST /admin/proxy-pools; GET /proxy/pools;
- POST /proxy/candidates/import; candidate management under /proxy/candidates.

There is no universal /v1 prefix. See PROXY_CANDIDATES.md for the real-proxy
acceptance procedure. A completed job or a batch successful count does not
prove HTTP success, absence of blocking, correct proxy choice or correct content.

## Health and diagnostics

```bash
docker compose ps -a
docker compose logs --tail=100 api worker initialize
curl -fsS http://127.0.0.1:8000/readyz
```

/healthz is liveness only. /readyz checks DB, Redis and a fresh worker capability
heartbeat including real browser launch checks. s3=disabled is normal in the
default stack; it does not establish WARC availability. The worker healthcheck
uses arq, not an HTTP request to the API process. API is loopback-only; remote
access needs an SSH tunnel or a properly secured HTTPS reverse proxy.

Do not repeatedly restart on a target-site 403. Inspect job error, HTTP status,
blocked, block_reason, engine, proxy_id and retries_used first. Do not automatically
reset circuit breakers or remove country restrictions to make a test pass.

## Durable attempt audit

One request_log row represents one transport attempt, not one logical job.
Docker logs and Prometheus aggregates do not replace the PostgreSQL history.
URLs in this history and downloaded bodies may contain sensitive data.

```sql
SELECT requested_at, job_id, attempt_number, escalation_tier, domain,
       proxy_id, proxy_pool_id, proxy_provider, proxy_type, proxy_country,
       engine, outcome, status_code, blocked, block_reason, duration_ms,
       bytes_received, error_type
FROM request_log
ORDER BY requested_at DESC LIMIT 100;

SELECT * FROM proxy_usage_daily ORDER BY usage_date DESC, attempts DESC;
```

For a full trace, filter request_log by a specific job_id and order by
attempt_number. Alert on increases in crawler_request_log_write_failures_total:
a successful crawl is not proof its independent audit write was persisted.
Migration 0008 adds a default partition; future yearly partitions are a
management optimization, not a prerequisite for accepting every timestamp.

## Backup before updates

Use a private directory outside the checkout. Avoid credential rotation during
the paired backup. For an application-consistent maintenance snapshot, stop
accepting new jobs and let active jobs finish before stopping api/worker.
A live pg_dump is a database snapshot, not a synchronized backup of Redis jobs.

```bash
umask 077
BACKUP=$(mktemp -d "$HOME/crawler-backup.XXXXXX")
docker compose exec -T postgres pg_dump -U crawler -d crawler -Fc > "$BACKUP/database.dump"
docker compose exec -T --user 0 api tar -C /run/crawler-secrets -czf - . > "$BACKUP/runtime-secrets.tar.gz"
test -s "$BACKUP/database.dump"
docker compose exec -T postgres pg_restore --file=/dev/null < "$BACKUP/database.dump"
gzip -t "$BACKUP/runtime-secrets.tar.gz"
sha256sum "$BACKUP/database.dump" "$BACKUP/runtime-secrets.tar.gz" > "$BACKUP/SHA256SUMS"
```

The credentials archive contains plaintext secrets. Store it securely with the
database backup, never in Git or public release assets. PostgreSQL custom dumps
do not back up Redis queues or container volumes. Define Redis/AOF backup and
job replay policy separately if queued work must survive a disaster.

## Non-destructive database restore drill

A read-through or archive listing is not a restore drill. On an isolated test
stack, use an unused database name and restore into that database only:

```bash
docker compose exec -T postgres createdb -U crawler restore_verify
docker compose exec -T postgres pg_restore -U crawler --dbname=restore_verify --exit-on-error < "$BACKUP/database.dump"
docker compose exec -T postgres psql -U crawler -d restore_verify -v ON_ERROR_STOP=1 -c 'SELECT * FROM alembic_version; SELECT count(*) FROM api_keys;'
docker compose exec -T postgres dropdb -U crawler restore_verify
```

Run commands sequentially and stop on any failure. Never drop or restore over
crawler to perform this test. The unified CI restores its disposable database
into ci_restore_verify, checks migration 0009 and selected row counts, then
removes the test database. Only an actual successful CI run proves that drill
passed for a particular revision. Production recovery also needs credentials,
roles, Redis state and application-level verification; this basic drill is not
proof of an entire disaster recovery plan.

## Updates and existing deployments

Back up first. Review release/migration changes, then git pull --ff-only and
docker compose up -d --build --wait during a maintenance window. Initializer
applies pending migrations. Docker caches may be evicted; updates may download
large layers. Keep a verified previous image and data backup. A destructive
schema migration cannot safely be undone by merely choosing an older image.

For the existing external Ubuntu stack, follow UBUNTU.md instead. Preserve its
Compose project name, file list, database identity, secrets and volumes. The
fresh-install Compose file does not adopt those resources automatically.

## Optional features and acceptance limits

Default WARC/S3, callbacks, Webshare sync and premium proxy tiers are off. Do not
claim those integrations passed because readiness is green. Enabling WARC needs
storage configuration, durable DLQ storage and a real archive-write/read test.
Callbacks need a secret and delivery/ownership/network-policy tests. A licensed
GeoIP database must be mounted read-only and maintained separately if selected.
Native ARM64 artifacts, sustained-load testing, network egress isolation and a
public-production security audit are not established by the default smoke test.
Historical PROJECT-HISTORY.md and ADRs describe earlier decisions; current code,
OpenAPI and successful checks for the exact revision take precedence.
