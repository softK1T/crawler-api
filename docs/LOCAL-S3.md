# Local S3 and WARC

This is the authoritative archival guide for canonical `docker-compose.yml`.

## Defaults and security

WARC is enabled. Local S3 is `rustfs/rustfs:1.0.6`, endpoint `http://s3:9000`,
bucket `crawler-warc`, region `us-east-1`. The S3 port is Docker-internal only.
The console binds to `127.0.0.1:${S3_CONSOLE_PORT:-9001}`; use an SSH tunnel.
Random credentials persist in `s3-secrets` and are never printed or committed.
The dedicated local server currently shares one administrative pair with the
application: do not share it with untrusted applications. Introduce scoped IAM
credentials before expanding access. Never delete secrets to rotate them.
Missing credentials with existing storage fail closed, rather than resetting.

## Archival contract

Every non-blocked capture writes a complete gzip response WARC to an atomic,
checksummed, fsynced disk journal BEFORE S3 upload. Each object is independent;
new captures do not use revisit deduplication or in-memory rotation windows.
Legacy rotation thresholds no longer control this writer. Existing archives
are not changed. Raw response headers accompany raw transport bytes.

Only successful S3 upload AND index commit remove the journal entry. Recovery
reuses the object key and index UUID (PostgreSQL ON CONFLICT DO NOTHING).
This is at-least-once delivery, not a distributed transaction. Worker recovery
runs at startup and every minute, up to ten entries per pass. S3/DB failure
fails the fetch job; subsequent archival recovery does not change that job's
status or repeat the fetch. The archive may therefore appear after job failure.

The 5 GiB journal cap includes base64/JSON overhead. Full storage rejects new
captures without evicting old ones. Corrupt entries are retained and logged;
operator repair is required. Interrupted `.tmp` files and legacy `warc/dlq.py`
files are not replayed automatically. Monitor logs, backlog and disk space.
Readiness performs authenticated HeadBucket with a timeout; smoke tests also
exercise actual writes/readback. HeadBucket alone is not a write audit.

## Existing local-stack upgrade

Keep the same Compose project and volumes. No DB migration or credential
rotation is added. Do not use `down -v` or `prune`.

```sh
git pull --ff-only origin master
docker compose -p crawler-current -f docker-compose.yml config --quiet
docker compose -p crawler-current -f docker-compose.yml build api
docker compose -p crawler-current -f docker-compose.yml pull s3
docker compose -p crawler-current -f docker-compose.yml up -d --no-build --pull never --wait --wait-timeout 300
curl --fail http://127.0.0.1:8000/readyz
```

The S3 image is a new download. Dockerfile/dependencies/browsers are unchanged;
only application source layers require rebuilding when their cache survives.

## Tests and backups

Offline: `python -m unittest discover -s tests -p test_warc_spool_unit.py -v`.
Docker CI runs `scripts/warc_smoke.py` with synthetic responses, real S3,
PostgreSQL and authenticated archive API: gzip readback, duplicate captures,
S3/DB failure, replay and idempotency. It verifies negative readiness with S3
stopped, restarts S3/recreates the worker and reads the original archives.
This is not a live external-network fetch test.

Back up PostgreSQL/warc_index, `s3-data`, `s3-secrets`, `runtime-secrets` and
`warc-spool` consistently, with producers stopped and storage quiesced unless
using a separately validated online procedure. Restore-test elsewhere. Named
volumes survive container recreation, NOT disk loss. This single-node server
has no host redundancy and is not an off-host backup. No archive-expiry policy
is installed; monitor growth and keep off-host backups.
