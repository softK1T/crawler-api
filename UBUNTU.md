# Ubuntu deployment guide

## New installation

Use the repository's default docker-compose.yml and README.md. On Linux x86_64:

```bash
git clone --branch interview-stable https://github.com/softK1T/crawler-api.git
cd crawler-api
docker compose up -d --build --wait
```

After release changes are merged, the ordinary clone of master can be used.
Do not combine compose.ubuntu.yaml with the default file. The former is a legacy
standalone template for externally managed credentials and prebuilt images,
not the required installation path. See docs/runbook.md for current operations.

## Existing external server: recorded state, not an automatic migration

As observed on 2026-11-19:
- Compose project: crawler-smoke-1795031146-2996236.
- Directory: /home/kit/crawler-stack.eBCDGC.
- Files: compose.yaml and compose.proxy-a21a48e.yaml in that directory.
- API/worker image: crawler-api:proxy-a21a48e (code a21a48e).
- Database revision: 0009.
- API binding: 127.0.0.1:32770 -> 8000.
- Readiness reported DB, Redis, worker, Playwright and Camoufox OK; S3 disabled.

These values are a dated operational record, not portable defaults. Later Git
commits do not update these running containers. Manage this stack explicitly:

```bash
docker compose --project-directory /home/kit/crawler-stack.eBCDGC \
  -p crawler-smoke-1795031146-2996236 \
  -f /home/kit/crawler-stack.eBCDGC/compose.yaml \
  -f /home/kit/crawler-stack.eBCDGC/compose.proxy-a21a48e.yaml ps -a
```

Do not start a replacement stack from another checkout assuming its fresh empty
volumes contain the same data. Do not use down -v or replace the database password.
Before adopting the new canonical stack:
1. Inventory actual mounts, database identity, queue DB, image IDs and secrets privately.
2. Quiesce API submissions, finish active jobs and make a current backup.
3. Restore into an isolated project with a different port; preserve or explicitly
   migrate existing API keys, credentials, proxy pools and domain policies.
4. Validate auth, ownership, migrations, browsers, attempt audit and representative jobs.
5. Switch traffic only after validation, retain rollback assets and record final paths.

The session's backup directory was /home/kit/crawler-backup-a21a48e.AYbVZ9.
A dated backup is not a current backup once more writes occur. Its archive read
check was successful; a production restore or migration was not thereby performed.
Never paste full environment files, keys, credentials archives or raw proxy URLs
into issues or logs. This guide does not claim public-production security readiness.
