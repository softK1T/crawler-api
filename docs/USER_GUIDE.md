# Usage guide

[Documentation](README.md) · [API reference](API.md) · [Deployment](runbook.md)

## 1. Check the service and authenticate

Start with the deployment guide. These Bash examples use `curl` and `jq`; host `jq` is only needed for these client examples, not to start Docker. Run commands sequentially and stop on errors.

```bash
BASE=http://127.0.0.1:8000
API_KEY=$(docker compose -p crawler-current -f docker-compose.yml exec -T api python scripts/stack_runtime.py key)
curl --fail-with-body "$BASE/readyz" | jq .
```

Expect `status: ready` and `s3: ok` in the canonical stack. Do not print the bootstrap key or enable `set -x`. It is an operator credential, not a normal client key.

## 2. Submit a page

This example explicitly disables proxy use. Mode selection is still subject to domain policy and escalation behavior.

```bash
JOB_ID=$(curl --fail-with-body "$BASE/v1/fetch" \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"url":"https://example.org/","mode":"static","use_proxy":false}' \
  | jq -er '.job_id')
curl --fail-with-body "$BASE/v1/jobs/$JOB_ID" -H "X-API-Key: $API_KEY" | jq .
```

HTTP 202 means accepted, not fetched. Poll the same job until `status` is `completed` or `failed`; do not resubmit just because it is still running. For a bounded synchronous wait, submit `"options":{"sync":true}`. If the API still returns 202, continue polling.

| Check | Why it matters |
|---|---|
| Job `status` and `error` | Polling can return HTTP 200 for a failed job |
| Result `status_code` | A completed task can contain a target HTTP error |
| `blocked` / `block_reason` | A challenge page can have target HTTP 200 |
| `engine`, `proxy_id`, `proxy_country` | Verify which transport/routing actually ran |
| Decoded body | Transport success does not prove the expected page was returned |

`idempotency_key` is a JSON request field scoped to the application. It is useful for replaying submission, but is not an exactly-once transaction guarantee.

## 3. Read the response and archive

Job results are temporary Redis data. Save a completed job response privately if needed:

```bash
umask 077
curl --fail-with-body "$BASE/v1/jobs/$JOB_ID" -H "X-API-Key: $API_KEY" > job.json
docker compose -p crawler-current -f docker-compose.yml exec -T api python -c 'import base64,hashlib,json,sys; r=json.load(sys.stdin)["result"]; b=base64.b64decode(r["body_b64"],validate=True); assert len(b)==r["body_bytes"]; assert hashlib.sha256(b).hexdigest()==r["content_sha256"]; sys.stdout.buffer.write(b)' < job.json > page.html
```

Only decode after checking completion and result presence. Inspect untrusted HTML as text first. Integrity checks do not establish semantic correctness.

Persistent archives use a different identifier from jobs:

```bash
curl --fail-with-body --get "$BASE/v1/archive/" \
  -H "X-API-Key: $API_KEY" --data-urlencode 'url=https://example.org/' | jq .
```

Use an entry's `id` with `GET /v1/archive/{entry_id}`. Its `body_b64` contains the extracted archived body. Filters must match the captured URL; redirects may change it. Archive scope currently exposes entries without application filtering, so keep it restricted to trusted operators.

## 4. Create a restricted client key

A tenant contains applications; a key belongs to one application. The following commands create resources and are not a read-only demo. Rerunning them can create extra tenants/keys or name conflicts.

```bash
umask 077
NAME="client-$(date +%s)"
TENANT_ID=$(curl --fail-with-body "$BASE/v1/tenants" \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg n "$NAME" '{name:$n}')" | jq -er '.id')
APP_ID=$(curl --fail-with-body "$BASE/v1/applications" \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg t "$TENANT_ID" '{tenant_id:$t,name:"client"}')" | jq -er '.id')
curl --fail-with-body "$BASE/v1/keys" \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg a "$APP_ID" '{application_id:$a,scopes:["fetch"],mode:"live"}')" > client-key.json
FETCH_KEY=$(jq -er '.raw_key' client-key.json)
```

Keep `client-key.json` private and outside version control. Use `FETCH_KEY` for that application's jobs and batches. It cannot read jobs submitted by the bootstrap application's key. Raw issued keys are returned once; `mode=test` is not a network sandbox. Creating an application does not isolate global proxy pools or domain policies.

## 5. Submit a batch

Use the client's key. Batch proxy overrides belong inside `options`:

```bash
curl --fail-with-body "$BASE/batches/" \
  -H "X-API-Key: $FETCH_KEY" -H 'Content-Type: application/json' \
  -d '{"urls":["https://example.org/"],"mode":"static","options":{"use_proxy":false}}'
```

Read `GET /batches/{batch_id}/status` and `/results` with the same application's key. Default admission allows up to 20 URLs; the schema caps the list at 100. Batch completion/success counts do not replace inspecting each job and page. Enqueueing is not an all-or-nothing transaction, and batch admission is not per-page pacing.

## Next tasks

- Routing and real proxy acceptance: [Proxy candidates](PROXY_CANDIDATES.md).
- Request fields, errors, key revocation/rotation: [API reference](API.md).
- Recovery and retention: [Local S3](LOCAL-S3.md).
- Never commit keys, cookies, raw proxy lists, or captured personal data.
