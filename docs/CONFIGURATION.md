# Configuration

[Documentation](README.md) · [Deployment](runbook.md) · [API](API.md)

## Two different configuration layers

1. Compose substitutes host/`.env` values only where `docker-compose.yml` references them.
2. Python settings read the environment inside each container.

Adding an arbitrary host `.env` value does not forward it to the API or worker. In particular, changing host `DATABASE_URL` does not replace the canonical stack's database: its entrypoint constructs the DSN from the persisted database secret.

## Supported host variables

| Variable | Default | Effect |
|---|---|---|
| `API_PORT` | `8000` | API host-loopback port; container port remains 8000 |
| `S3_CONSOLE_PORT` | `9001` | S3 console host-loopback port |
| `WORKER_METRICS_PORT` | `9464` | Worker metrics host-loopback port; container port remains 9464 |
| `WORKER_MAX_JOBS` | `2` | Maximum concurrent worker jobs |
| `PROXY_SYNC_ENABLED` | `false` | Allow provider sync; also requires credentials |
| `WEBSHARE_API_KEY` | Empty | Private Webshare API credential |
| `ENABLE_PREMIUM_PROXY_TIERS` | `false` | Enable premium escalation tiers; can incur provider charges |

Example: `API_PORT=8001 docker compose -p crawler-current -f docker-compose.yml up -d --no-build --pull never --wait`.

## Canonical container settings

| Setting | Canonical value | Notes |
|---|---|---|
| `ENABLE_WARC` | `true` | Archive routes enabled |
| `S3_ENDPOINT_URL` | `http://s3:9000` | Docker-internal RustFS service |
| `S3_BUCKET` / `S3_REGION` | `crawler-warc` / `us-east-1` | Bucket is checked/created at application startup |
| S3 credentials | Loaded from `s3-secrets` | Do not replace with committed literals |
| `WARC_DLQ_DIR` | `/var/lib/crawler/warc-spool` | Persistent journal for the current writer |
| `WARC_DLQ_MAX_BYTES` | `5368709120` | 5 GiB, including journal serialization overhead |
| `PROXY_CHECK_ENABLED` | `true` | Background candidate checker |
| `BROWSER_POOL_SIZE` | `1` | Chromium pool size, not total browser memory |
| `WORKER_METRICS_PORT` | `9464` | Worker only: port of its Prometheus endpoint. The Python default is `0`, which starts no endpoint |
| `REQUIRE_CAMOUFOX` / `SSRF_ENABLED` | `true` / `true` | Browser readiness and URL protection |
| `ENABLE_CALLBACKS` | `false` | Requires an explicit reviewed configuration change |

These values are not all host-overridable. Change the Compose environment deliberately if needed, preserving service volumes, credentials, and dependency order. Runtime-wide Python defaults live in `app/core/config.py`; candidate-checker settings live in `app/core/proxy_check_config.py`. A defined setting or request field is not proof every engine uses it.

## Limits and integrations

- Single-job and batch results are temporary Redis data; the default job-result TTL is 86400 seconds.
- Batch admission defaults to 20 URLs; its request schema caps the list at 100.
- Callbacks additionally need `CALLBACK_HMAC_SECRET` and separate delivery/egress validation.
- Provider sync requires private credentials and explicit enablement.
- Current WARC writes use one response per object. Legacy rotation-size/age flags do not control this writer.

[Archival contract and backups](LOCAL-S3.md) · [Security](SECURITY.md)
