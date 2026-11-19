# Deployment and operations

[Documentation](README.md) · [Configuration](CONFIGURATION.md) · [Security](SECURITY.md)

## Scope and prerequisites

This guide is for canonical `docker-compose.yml` on `master`, using Compose project `crawler-current`. Run commands from the repository root. Linux x86_64, Git, Docker/Compose v2, network access, and sufficient browser/archive disk space are required. Do not apply this recipe to a separately managed external stack without a migration plan.

No mandatory `.env`, host Python, manual database initialization, or prebuilt application image is needed. The first build can download large browser artifacts; later cache hits are not a download-free guarantee.

## Fresh installation

```bash
git clone --branch master https://github.com/softK1T/crawler-api.git
cd crawler-api
docker compose -p crawler-current -f docker-compose.yml build api
docker compose -p crawler-current -f docker-compose.yml pull postgres redis s3
docker compose -p crawler-current -f docker-compose.yml up -d --no-build --pull never --wait --wait-timeout 300
```

Run each command only after the preceding command succeeds. Keep the Compose project name stable: changing it can attach different named volumes.

| Service | Expected state | Responsibility |
|---|---|---|
| `secrets` | Exited (0) | Prepare persisted DB/S3 credentials and volume permissions |
| `postgres`, `redis`, `s3` | Healthy | Database, queue/state, WARC object storage |
| `initialize` | Exited (0) | Apply migrations and prepare the administrator key |
| `api`, `worker` | Healthy | Serve the API and execute queued jobs |

## Verify readiness

```bash
docker compose -p crawler-current -f docker-compose.yml ps -a
curl --fail-with-body http://127.0.0.1:8000/readyz
```

The canonical stack should report `status: ready` and `s3: ok`. `/healthz` only checks API process liveness. Worker health uses arq, not an API HTTP request. Browser capabilities come from a short-lived worker heartbeat. S3 readiness makes an authenticated bucket request; it does not prove arbitrary writes or a complete restore.

The API binds to `127.0.0.1:8000`, the S3 console to `127.0.0.1:9001`; S3 port 9000 is not published. Use SSH tunnelling or a secured reverse proxy for remote access. See [configuration](CONFIGURATION.md) to change host ports.

## Update an existing stack

Back up first, review changes/migrations, and use a maintenance window when needed. Preserve the project name, Compose file, volumes, and credentials.

```bash
git pull --ff-only origin master
docker compose -p crawler-current -f docker-compose.yml build api
docker compose -p crawler-current -f docker-compose.yml pull s3
docker compose -p crawler-current -f docker-compose.yml up -d --no-build --pull never --wait --wait-timeout 300
curl --fail-with-body http://127.0.0.1:8000/readyz
```

Only `api` builds the shared application image. The initializer applies pending migrations. Restarting an old image is not a safe rollback for an incompatible schema change. Keep a verified previous image and recoverable backups.

## Diagnose before changing state

| Symptom | Inspect first | Do not do |
|---|---|---|
| `secrets` exits nonzero | Its traceback, volume paths, persisted secret state | Delete secrets or data volumes |
| S3 healthy check fails | `s3` logs, ownership, port/address configuration | Replace existing credentials to force startup |
| `initialize` fails | Migration output and database availability | Reinitialize the database |
| API not ready | `/readyz` checks and API/worker logs | Treat `/healthz` alone as success |
| Job remains pending | Worker logs, queue identity, readiness | Blindly submit duplicate jobs |
| Target returns 403/challenge | Job error, HTTP status, block reason, engine and proxy | Disable guards or reset every breaker |

```bash
docker compose -p crawler-current -f docker-compose.yml logs --no-color --tail=120 secrets initialize api worker s3
```

Redact URLs, tokens, cookies, proxy credentials, and personal data before sharing logs. The S3 initializer accepts only known empty image directories when creating its first credentials; actual files or partial secrets still require operator recovery. Do not bypass this safeguard for an existing storage volume.

## Credentials

```bash
API_KEY=$(docker compose -p crawler-current -f docker-compose.yml exec -T api python scripts/stack_runtime.py key)
```

Never echo the key or enable shell tracing. Bootstrap has `admin`, `keys`, `fetch`, and `archive`; issue narrower client keys. Normal initialization reuses persisted credentials. Invalid/revoked bootstrap keys are not silently replaced. Host `API_KEYS_RAW` does not substitute for database-backed authentication.

## Back up during a maintenance window

Before this procedure, stop incoming submissions and choose a queue-drain/replay policy. It backs up PostgreSQL and mounted archival/secret state, NOT Redis queue/AOF state. Back up Redis separately if queued work must be recovered.

Stop producers first, then storage, and keep them stopped until the paired backup is complete:

```bash
umask 077
BACKUP=$(mktemp -d "$HOME/crawler-backup.XXXXXX")
docker compose -p crawler-current -f docker-compose.yml stop api
docker compose -p crawler-current -f docker-compose.yml stop worker
docker compose -p crawler-current -f docker-compose.yml stop s3
docker compose -p crawler-current -f docker-compose.yml exec -T postgres pg_dump -U crawler -d crawler -Fc > "$BACKUP/database.dump"
docker compose -p crawler-current -f docker-compose.yml run --rm --no-deps -T --pull never --user 0 --entrypoint tar secrets -czf - -C / run/crawler-secrets run/crawler-s3 var/lib/crawler-s3 var/lib/crawler/warc-spool < /dev/null > "$BACKUP/volumes.tar.gz"
test -s "$BACKUP/database.dump"
gzip -t "$BACKUP/volumes.tar.gz"
sha256sum "$BACKUP/database.dump" "$BACKUP/volumes.tar.gz" > "$BACKUP/SHA256SUMS"
```

Stop on any failure; retain the stopped state while diagnosing an incomplete backup. The archive contains plaintext credentials and captured data. Encrypt/protect it off-host. The temporary `secrets` container above overrides the entrypoint with `tar`; it does not generate or rotate credentials. These checks establish archive readability, not a successful restore.

After completing the backup, bring the stack back with `up -d --no-build --pull never --wait --wait-timeout 300` using the same project/file arguments. Do not use `down -v` or `prune`.

## Validate recovery separately

Restore into a separate deployment, never over the working database for a test. Validate migrations, credentials, selected row counts, S3 object availability, pending-journal recovery, archive body readback, and job behavior. A database-only restore drill does not validate a complete archival backup.

For a synthetic archival round trip on a healthy test stack:

```bash
docker compose -p crawler-current -f docker-compose.yml exec -T worker python scripts/stack_s3_runtime.py python scripts/warc_smoke.py
```

This writes synthetic records, tests gzip readback and simulated S3/DB failures, and checks replay. It does not fetch a live target or certify whole-system disaster recovery. Do not point pytest at deployment databases/Redis: fixtures can drop tables and flush data.

[WARC durability details](LOCAL-S3.md)
