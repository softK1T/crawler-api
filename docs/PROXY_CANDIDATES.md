# Proxy candidates and ten-page acceptance

## Contract

The checker performs a bounded HTTPS control request through each enrolled proxy.
It measures exit IP and country; it does not establish anonymity, residential
classification, browser compatibility or access to OLX or any other site.
The default Compose enables the checker. Other deployments must explicitly set
PROXY_CHECK_ENABLED=true in the worker and apply migration 0009 first.

Defaults: control endpoint https://1.1.1.1/cdn-cgi/trace, 5-second probe timeout,
concurrency 50, batch size 250, success TTL 21600 seconds and retry delay 600 seconds.
Cron checks twice per minute and caps each pass near 25 seconds. Large imports need multiple passes. Without an MMDB,
country comes from trace loc, not colo; the endpoint is not a geolocation SLA.
A licensed GeoLite2-Country database may be mounted read-only using
PROXY_CHECK_DATABASE_PATH. Do not commit or redistribute that database casually.

## Correct API routes

There is no shared /v1 prefix for these routes. All candidate operations need admin:

- POST /admin/proxy-pools with name and provider=public-candidates.
- POST /proxy/candidates/import with pool_id and proxies (1..500 URL strings).
- GET /proxy/candidates/pools/{pool_id}/results?limit=500&offset=0.
- GET /proxy/candidates/pools/{pool_id}/stats.
- POST /proxy/candidates/pools/{pool_id}/recheck.
- PATCH /proxy/candidates/{proxy_id}/enabled with {"enabled":false} or true.

Accepted candidate hosts are public IP literals, not DNS gateway names. Supply
full HTTP/HTTPS/SOCKS URLs; URL-encode authentication. Never paste real credential
URLs into chat. New records are inactive, managed, pending and type unknown.
Global uniqueness is (provider, URL): duplicates are not moved to another pool or
reactivated. Reimport/recheck does not unpause a manually paused candidate.
Use enabled=false rather than changing is_active alone to persist a pause.
Existing paid-provider rows are not automatically enrolled by the importer.

Results omit proxy URLs and credentials. A valid HTTPS response with unknown
country may activate a candidate, but it cannot match PL. Failure disables it;
stale checks are excluded. Sticky and normal selections apply the same pool,
country, type, cooldown and managed-check freshness requirements.

## Real acceptance: operator-supplied inputs

Status: this is a procedure, not a claim a live OLX run has passed. Run in an
isolated test stack, with authorized proxies and ten chosen permitted public
URLs. Review site terms and robots.txt. Do not use login, seller contact data,
private APIs, CAPTCHA bypass or repeated retries against a refusal.
The sequence is:

1. Start the canonical stack. Capture its administrative key privately:

```bash
API_KEY=$(docker compose exec -T api python scripts/stack_runtime.py key)
BASE=http://127.0.0.1:8000
```

2. Create a separate application and fetch-only key through /v1/applications and
/v1/keys if testing tenancy; the bootstrap key supplies admin/keys privileges.
Check missing-key requests return 401 and fetch-only access to admin routes 403.

3. Create a candidate pool, save its returned UUID and import your private URL
array through /proxy/candidates/import. Do not assume an imported count means
working proxies. Wait for results with check_status=ready, is_active=true,
check_auto_activate=true, country=PL and unexpired check_due_at. If no PL proxy
passes, stop; do not relax the country or fall back to direct access silently.

4. Snapshot any existing olx.pl domain policy before changing it. Policies/pools
are global; a new application does not isolate them. POST /admin/domain-policies
is an upsert and can replace configuration. For an isolated single-attempt test:

```json
{
  "domain": "olx.pl",
  "proxy_pool_id": "<returned pool UUID>",
  "engine": "httpx",
  "respect_robots": true,
  "max_retries": 1,
  "max_escalation_attempts": 1,
  "escalation_tier": 0,
  "tier_locked": true
}
```

5. POST /batches/ with exactly ten distinct actual URLs, mode=static and:

```json
{"use_proxy":true,"proxy_country":"PL","proxy_type":"unknown"}
```

Place this object inside options, not at the top level. unknown is the current
candidate type and a string worker override in options, not an inferred commercial
proxy class. A datacenter/residential filter will not select these candidates.
The domain policy binds the pool; options.proxy_pool_id is not a supported pool
selector. A domain engine policy can override the request's mode.

6. Save batch_id and job_ids. Poll GET /batches/{batch_id}/status until all ten jobs
are terminal, then save GET /batches/{batch_id}/results to a private JSON file.
An HTTP failure while submitting a batch is not permission to submit it again
blindly: admission is not an atomic transaction with all queue writes.

7. For each job verify completed, HTTP 2xx, blocked=false, intended proxy_id/pool,
expected engine and attempt budget. Decode body_b64, compare decoded length with
body_bytes and SHA256 with content_sha256, then inspect actual page content.
Check request_log for the job_id to confirm all transport attempts and proxy
metadata, not just the final result. Protect URLs, HTML and errors as potentially
sensitive data. Ten completed jobs are not ten valid pages.

8. Restore the previous policy only after all test jobs finish. Do not restore it
while queued jobs may still read it. Revoke temporary application keys using a
different key with keys scope; self-revocation is forbidden. Retain safe audit IDs.

WORKER_MAX_JOBS=1 limits that worker, not global domain traffic. Batch admission
checks the domain once; rate_limit_rps does not prove per-page pacing. One weighted
picker may reuse an IP. HTTPX failure does not by itself establish a broken queue
or a need for browser fallback. A separate, explicit browser acceptance run is
needed after establishing the base path.

## Automated coverage

Unit/integration tests cover normalization, activation, pause, freshness and
selection, using mocked probes. Unified CI covers clean startup and pool creation,
not successful real proxies or target-site access. Use disposable DB/Redis for
pytest; fixtures delete data. Do not execute tests against the server deployment.
