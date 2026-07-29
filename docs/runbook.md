# Operator Runbook

## Onboarding

```bash
git clone https://github.com/softK1T/crawler-api.git
cd crawler-api
cp .env.example .env
# Edit .env: set DATABASE_URL, REDIS_URL, API_KEYS_RAW, S3 keys
docker compose up -d
curl http://localhost:8000/healthz   # verify
```

## Day-to-Day Operations

- **Health:** `/healthz` (liveness), `/readyz` (DB/Redis/S3 check)
- **Metrics:** `/metrics` — Prometheus scrape endpoint
- **Logs:** `docker compose logs -f api worker` — structlog JSON, single-line

## Common Tasks

### Rotating API Keys
```
POST /v1/keys with SCOPE_KEYS scope → returns raw_key once
DELETE /v1/keys/{old_key_id} to revoke
```

### Adding Domain Policies
```
POST /v1/admin/domain-policies (SCOPE_ADMIN)
Body: {"domain":"example.com","engine":"playwright","rate_limit_rps":2.0}
```

### Managing Proxy Pools
```
POST /v1/admin/proxy-pools → create pool
POST /v1/admin/proxy-pools/{id}/proxies → add proxy
GET /v1/proxy/proxies → list with health scores
```

### Checking Usage
```
GET /v1/usage → caller's usage
GET /v1/usage/applications/{id} → admin view (SCOPE_ADMIN)
```

## Incident Response

### Elevated block_rate_total on a domain
1. Check domain policy: `GET /v1/admin/domain-policies?domain=...`
2. Switch engine (httpx → curl_cffi → playwright)
3. Rotate proxy pool or add fresh proxies
4. Reset circuit breaker: `DELETE /v1/proxy/circuit-breaker/{domain}`

### Queue depth stuck high
1. `docker compose logs worker` — check for errors
2. `docker compose restart worker`
3. Check Redis memory: `docker compose exec redis redis-cli INFO memory`

### S3/MinIO failure
- WARC data loss is accepted during S3 outages — the primary store is PostgreSQL.
- Check MinIO: `curl http://localhost:9000/minio/health/live`
- Restart MinIO: `docker compose restart minio`

### Database connection errors
1. `docker compose exec db pg_isready -U crawler -d crawlerdb`
2. Check disk: `docker compose exec db df -h /var/lib/postgresql/data`
3. Check connection pool exhaustion: reduce API worker concurrency

## Maintenance

### Adding new request_log partitions (annually)
```sql
CREATE TABLE request_log_y2028 PARTITION OF request_log
FOR VALUES FROM ('2028-01-01') TO ('2029-01-01');
```
Run before December of the current year. See ADR-003.

### Rotating proxy credentials
1. Update proxy URLs in `/v1/admin/proxy-pools/{id}/proxies`
2. Reset health scores: `POST /v1/proxy/reset/{proxy_id}`
3. Old proxies with invalid credentials will auto-cool down via health decay.

### Updating Python/OS
1. Update Dockerfile base image version
2. Rebuild: `docker compose build --no-cache`
3. Test with `./scripts/verify.sh`

### Backup
- PostgreSQL: `docker compose exec db pg_dump -U crawler crawlerdb > backup.sql`
- WARC files are in S3/MinIO — ensure bucket versioning or lifecycle policies are configured.

## Proxy Sync Cron (Stage 14)

Webshare proxies are synced every 30 minutes via arq cron (`sync_proxies`).
**Manual trigger:**
```bash
docker compose exec worker python3 -c "
import asyncio
from app.worker.tasks.proxy_sync import sync_proxies
from app.worker.tasks.fetch_task import startup, shutdown
async def run():
    ctx = {}
    await startup(ctx)
    await sync_proxies(ctx)
    await shutdown(ctx)
asyncio.run(run())
"
```

**Reading the summary log event:**
The task logs `sync_proxies: done added=X updated=Y deactivated=Z total_fetched=N`.
- `added`: new proxies inserted.
- `updated`: previously inactive proxies reactivated.
- `deactivated`: proxies no longer in the Webshare list were set `is_active=False`.
  They are never hard-deleted — FK constraints from `request_log` and health
  history depend on the row surviving.

## Browser Mode (Stage 14)

Playwright ("browser"/"camoufox" modes) runs **browser-per-fetch** — each request
launches a fresh browser and context.  There is no pooling.  This caps throughput
at ~1 req/s per worker.  A bounded browser pool is planned for Stage 15.
