# Ubuntu interview baseline

## Components

API, arq worker, PostgreSQL and Redis.
Engines: httpx, curl_cffi, Playwright Chromium and Camoufox Firefox.
WARC, callbacks, premium proxy tiers, proxy sync and tracing are disabled.
API is published on loopback only.

## Existing server deployment

Git checkout: /home/kit/projects/crawler-api
Active Compose directory: /home/kit/crawler-stack.eBCDGC

These are separate directories. A git pull does not update containers.
Do not start another stack from the checkout accidentally.

Run management commands from the active Compose directory:

- docker compose ps -a
- docker compose logs --tail=80 api worker
- docker compose port api 8000
- docker compose exec -T worker arq --check app.worker.arq_worker.WorkerSettings

The active stack uses a dynamically assigned API port.
The worker healthcheck must use arq --check, not the API HTTP endpoint.

## Repository template

compose.ubuntu.yaml is a standalone template.
Do not combine it with docker-compose.yml.

Variables:
- POSTGRES_PASSWORD: required; use a long random hexadecimal password.
- CRAWLER_IMAGE: default crawler-api:interview-ubuntu-check.
- API_PORT: default 8000; bound to 127.0.0.1.

Build the application image separately before using the template.
For a fresh deployment: start PostgreSQL and Redis, apply Alembic migrations,
then start API and worker. Bootstrap credentials privately: bootstrap_dev.py
prints the raw API key.

When adopting the template for an existing deployment, preserve the Compose
project name and database credentials. Do not create replacement volumes.

## Safety

Keep .env, passwords and API keys outside Git.
Never run tests against deployment PostgreSQL or Redis:
test fixtures drop tables and flush Redis.
Do not run docker compose down -v unless data deletion is intentional.
Dependencies are not fully locked; retain the verified image for rollback.

## Verification

Regression run on macOS: 281 passed, 1 deselected.
Native browser integration file excluded.
Ruff, formatting and Mypy passed.

Ubuntu checks performed:
- Four engines completed jobs through API and worker.
- Batch completion, results, ownership and size limit.
- Missing, empty and short API keys returned 401.
- Rotation privilege checks, expiry inheritance and overlap.
- Both browser guards blocked loopback navigation and image subresources.
- A reachable local server provided positive control for guard checks.

The later image built from commit a844264 passed browser smoke and API
startup/authentication checks. The complete earlier end-to-end suite
was not repeated on that image.

This is a demonstration baseline, not a complete production security audit.
DNS rebinding, redirect coverage, sandboxing, network-level egress isolation
and load testing require further verification.
GitHub Actions status must be checked separately.
