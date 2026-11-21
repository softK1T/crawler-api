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

## 6. Compare fetch modes

`scripts/benchmark.py` sends every page in `scripts/data/benchmark_targets.txt` through each mode and prints two Markdown tables: one line per mode, then successes per target. It submits with `use_policy: false`, so earlier results do not move a mode's starting tier.

```bash
docker compose -p crawler-current -f docker-compose.yml exec -T api python scripts/benchmark.py --no-proxy
```

| Column | Meaning |
|---|---|
| OK | Jobs whose body contains the target's marker text, out of all jobs |
| p50 s, p95 s | Job time (`completed_at` minus `created_at`) of successful jobs only |
| Attempts/job | Mean attempts across the escalation ladder, failed jobs included |
| Delivered by | Engine that produced the body of each successful job |
| Failures | Block reason, error code, `http_<status>`, or `marker_missing` |

Drop `--no-proxy` once the proxy pool has healthy proxies; with an empty pool every mode above `static` fails with `PROXY_POOL_EMPTY`. The default run submits 84 jobs to public practice sites under the bootstrap key's rate limits and takes several minutes; `--runs`, `--modes` and `--targets` narrow it. The numbers describe this host and its network at that moment, so quote only a run you made.

## 7. Collect an OLX listing

`scripts/olx_dataset.py` walks an OLX category or search page by page in `static` mode and writes one row per ad to stdout: JSON lines, or CSV with `--csv`. Progress and the quality report go to stderr. Rows follow `Listing` in `scripts/olx_parser.py`; seller names, contact details and descriptions are not collected.

```bash
docker compose -p crawler-current -f docker-compose.yml exec -T api python scripts/olx_dataset.py https://www.olx.pl/elektronika/telefony/ --pages 3 --no-proxy > phones.jsonl
```

The report counts the repeats dropped: the same ad shows up in paid slots on several pages, in a paid slot and in the organic list, or twice in the organic list when it shifts between page requests. It then says how often each field is empty and why a price is missing: a free item, an exchange offer, or no price given, as in jobs and services. A page that is blocked, fails or has no listing ends the walk: the rows collected before it are still written and the exit code is 1. OLX serves at most 25 pages for one listing: 40 ads a page plus up to 12 paid slots.

## Next tasks

- Routing and real proxy acceptance: [Proxy candidates](PROXY_CANDIDATES.md).
- Request fields, errors, key revocation/rotation: [API reference](API.md).
- Recovery and retention: [Local S3](LOCAL-S3.md).
- Never commit keys, cookies, raw proxy lists, or captured personal data.
