# User guide

[Documentation](README.md) · [API reference](API.md) · [Proxy acceptance](PROXY_CANDIDATES.md)

## 1. Start and authenticate

Start from the project README. The examples below use Bash, curl and jq; jq is
an optional client-side convenience, not a requirement for starting Docker.
Run sequentially and stop on HTTP/JSON errors. Resource creation is not generally
idempotent: rerunning the whole guide can create additional tenants and keys.

```bash
umask 077
BASE=http://127.0.0.1:8000
API_KEY=$(docker compose exec -T api python scripts/stack_runtime.py key)
curl -fsS "$BASE/readyz" | jq .
```

Expected readiness is ready. The bootstrap key is a privileged local administrator.
Never publish it or enable set -x. The normal API always needs a valid DB-backed
key; an empty API_KEYS_RAW does not make these endpoints anonymous.

## 2. Create a client identity

A tenant groups applications. An application owns its jobs, batch metadata and
usage. A key belongs to an application and carries scopes. Domain policies and
proxy pools are global, not isolated by creating a new application.

```bash
RUN="demo-$(date +%s)"
TENANT_ID=$(curl -fsS -X POST "$BASE/v1/tenants" \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg name "$RUN" '{name:$name}')" | jq -er '.id')
APP_ID=$(curl -fsS -X POST "$BASE/v1/applications" \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg tenant "$TENANT_ID" '{tenant_id:$tenant,name:"demo-client"}')" | jq -er '.id')
curl -fsS -X POST "$BASE/v1/keys" \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg app "$APP_ID" '{application_id:$app,scopes:["fetch"],mode:"live"}')" \
  > client-key.json
FETCH_KEY=$(jq -er '.raw_key' client-key.json)
KEY_ID=$(jq -er '.id' client-key.json)
```

client-key.json contains the raw credential and must remain private. The API returns
a raw issued key once. mode=test is a key tag, not a no-network sandbox. A fetch-only
key cannot perform administrative operations. Prefer expiring keys for real clients.

## 3. Submit one page

```bash
JOB_ID=$(curl -fsS -X POST "$BASE/v1/fetch" \
  -H "X-API-Key: $FETCH_KEY" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg token "$RUN-first" '{url:"https://example.com/",mode:"static",use_proxy:false,idempotency_key:$token}')" \
  | jq -er '.job_id')
```

202 means accepted, not downloaded. The request uses an explicit direct connection;
no proxies are bundled with the service. A domain policy can influence engine and
retry behaviour. Do not rely on mode=static as a universal lock on every later tier.

```bash
for attempt in $(seq 1 90); do
  curl -fsS "$BASE/v1/jobs/$JOB_ID" -H "X-API-Key: $FETCH_KEY" > job.json
  STATUS=$(jq -er '.status' job.json)
  case "$STATUS" in completed|failed) break ;; esac
  sleep 2
done
jq 'del(.result.body_b64)' job.json
```

If still pending/running after this bounded wait, inspect the queue/worker; do not
blindly submit another job. Idempotency is application-scoped and does not promise
an atomic exactly-once transaction across Redis, queue submission and side effects.

## 4. Validate and save the response

Check completed, expected HTTP status, blocked=false and the actual page. A 200
challenge page can still be wrong. The following decodes and checks integrity
using Python already installed in the API image, not on the host:

```bash
docker compose exec -T api python -c 'import base64,hashlib,json,sys; j=json.load(sys.stdin); r=j["result"]; b=base64.b64decode(r["body_b64"],validate=True); assert len(b)==r["body_bytes"]; assert hashlib.sha256(b).hexdigest()==r["content_sha256"]; sys.stdout.buffer.write(b)' \
  < job.json > page.html
```

Integrity is not semantic success. Inspect untrusted HTML as text first, not in a
trusted logged-in browser session. Results/bodies can contain personal information.

## 5. Submit a batch

Create urls.json containing an array of permitted actual URLs. Up to 20 are allowed
by the default installation; the schema also has a hard cap of 100.

```bash
jq -n --slurpfile urls urls.json \
  '{urls:$urls[0],mode:"static",options:{use_proxy:false}}' > batch.json
curl -fsS -X POST "$BASE/batches/" \
  -H "X-API-Key: $FETCH_KEY" -H 'Content-Type: application/json' \
  --data-binary @batch.json > batch-created.json
BATCH_ID=$(jq -er '.batch_id' batch-created.json)
curl -fsS "$BASE/batches/$BATCH_ID/status" -H "X-API-Key: $FETCH_KEY" | jq .
curl -fsS "$BASE/batches/$BATCH_ID/results" -H "X-API-Key: $FETCH_KEY" > batch-results.json
```

Poll status until all jobs are terminal before evaluating final counts. successful
counts completed jobs, not necessarily unblocked 2xx pages. Each URL consumes
key/application admission; the domain admission check occurs once per unique domain.
The batch endpoint does not guarantee per-page pacing or transactional all-or-nothing
enqueueing. Request-level proxy overrides belong inside options for batches.

## 6. Add real proxies or browsers

Read the proxy acceptance guide before using candidate pools. A PL requirement
needs fresh passing PL candidates; type unknown is not residential/datacenter.
Binding a domain policy to the intended pool avoids unintended pool participation.
use_proxy=true must not be treated as permission to fall back to the server's IP.

Available modes are static, stealth, browser and camoufox. They select HTTPX,
curl_cffi, Playwright and Camoufox subject to domain policy and escalation rules.
Having a browser installed does not guarantee access to a protected target.
Use only permitted targets and authorized proxies; do not defeat a refusal by
removing safety filters, solving CAPTCHA automatically or ignoring site limits.

## 7. Observe and finish

GET /v1/usage/ with the client key reports its application's usage. request_log
contains individual attempts in PostgreSQL; job results have a finite Redis TTL.
Usage/cost fields are estimates from service accounting, not provider invoices.

Revoke the demo fetch key with the separate administrator credential:

```bash
curl -fsS -X DELETE "$BASE/v1/keys/$KEY_ID" -H "X-API-Key: $API_KEY" | jq .
unset FETCH_KEY API_KEY
```

A key cannot revoke itself. Keep required credentials in a secret store, remove
unneeded plaintext local copies, and never commit client-key.json, HTML or raw
proxy lists. Administrative scope is global and must not be given to normal clients.
