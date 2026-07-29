#!/usr/bin/env bash
# verify.sh — end-to-end verification for crawler-api.
# Usage: ./scripts/verify.sh
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'
log()  { echo -e "${GREEN}[verify]${NC} $*"; }
err()  { echo -e "${RED}[verify]${NC} $*"; exit 1; }

cd "$(dirname "$0")/.."

# Use the project venv if it exists.
if [ -f .venv/bin/python3 ]; then
    export PATH="$(pwd)/.venv/bin:$PATH"
fi

export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://crawler:crawler@localhost:5432/crawlerdb}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"

# 1. Lint
log "ruff check..."
ruff check . || err "ruff check failed"
ruff format --check . || err "ruff format check failed"

# 2. Typecheck
log "mypy..."
mypy app/ tests/ || err "mypy failed"

# 3. Alembic — run via Docker compose against the compose DB.
log "alembic upgrade..."
docker compose run --rm -T api alembic upgrade head 2>/dev/null || err "alembic upgrade failed"

# 4. Tests
log "pytest..."
export DOCKER_HOST="${DOCKER_HOST:-unix://$HOME/.docker/run/docker.sock}"
export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE="${TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE:-/var/run/docker.sock}"
export TESTCONTAINERS_HOST_OVERRIDE="${TESTCONTAINERS_HOST_OVERRIDE:-localhost}"
export TESTCONTAINERS_RYUK_DISABLED="${TESTCONTAINERS_RYUK_DISABLED:-true}"
pytest -m "not slow" -q || err "pytest failed"

# 5. Docker build
log "docker build..."
docker build -t crawler-api-local . || err "docker build failed"

# 6. Compose up
log "docker compose up..."
docker compose up -d || err "compose failed"
sleep 5

# Re-run alembic after compose up (fresh DB).
docker compose run --rm -T api alembic upgrade head 2>/dev/null || true

# 7. Bootstrap auth — always runs inside container now that scripts/ is in the image.
log "bootstrap auth..."
TEST_KEY=$(docker compose exec -T api python3 scripts/bootstrap_dev.py 2>/dev/null | tail -1) || true
if [ -z "$TEST_KEY" ]; then
    err "Could not bootstrap auth key"
fi
log "Test key: ${TEST_KEY:0:16}..."

# 8. Health checks
log "/healthz..."
for i in $(seq 1 20); do
    curl -sf http://localhost:8000/healthz && break
    sleep 1
done
log "/healthz OK"

log "/readyz..."
for i in $(seq 1 20); do
    curl -sf http://localhost:8000/readyz && break
    sleep 1
done
log "/readyz OK"

# ── Operator key management workflow (ADR-016) ─────────────────────────────────

# 9. Create a tenant and application via the operator key.
log "Create tenant..."
TENANT_RESP=$(curl -sf -X POST http://localhost:8000/v1/tenants \
    -H "X-API-Key: ${TEST_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"name":"verify-tenant"}')
TENANT_ID=$(echo "$TENANT_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['id'])")
[ -n "$TENANT_ID" ] || err "Tenant: no id in response: $TENANT_RESP"
log "tenant_id=$TENANT_ID"

log "Create application..."
APP_RESP=$(curl -sf -X POST http://localhost:8000/v1/applications \
    -H "X-API-Key: ${TEST_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"tenant_id\":\"${TENANT_ID}\",\"name\":\"verify-app\"}")
APP_ID=$(echo "$APP_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['id'])")
[ -n "$APP_ID" ] || err "Application: no id in response: $APP_RESP"
log "application_id=$APP_ID"

# 10. Issue a key for that application.
log "Issue key..."
KEY_RESP=$(curl -sf -X POST http://localhost:8000/v1/keys \
    -H "X-API-Key: ${TEST_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"application_id\":\"${APP_ID}\",\"scopes\":[\"fetch\",\"archive\"],\"mode\":\"live\"}")
NEW_KEY=$(echo "$KEY_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['raw_key'])")
KEY_ID=$(echo "$KEY_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['id'])")
[ -n "$NEW_KEY" ] || err "Issue key: no raw_key in response: $KEY_RESP"
[ -n "$KEY_ID" ] || err "Issue key: no id in response: $KEY_RESP"
[ "$NEW_KEY" != "${TEST_KEY}" ] || err "Issued key must differ from operator key"
log "key_id=$KEY_ID prefix=${NEW_KEY:0:8}..."

# 11. Fetch with the issued key.
log "Fetch with issued key..."
FETCH_RESP=$(curl -s -X POST http://localhost:8000/v1/fetch \
    -H "X-API-Key: ${NEW_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"url":"http://example.com","mode":"static"}')
JOB_ID=$(echo "$FETCH_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('job_id',''))" 2>/dev/null)
[ -n "$JOB_ID" ] || err "Fetch: no job_id in response: $FETCH_RESP"
log "job_id=$JOB_ID"

# 12. Poll for completion.
log "Poll job..."
for i in $(seq 1 30); do
    S="$(curl -s "http://localhost:8000/v1/jobs/${JOB_ID}" \
        -H "X-API-Key: ${NEW_KEY}" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('status',''))
" 2>/dev/null)"
    if [ "$S" = "completed" ] || [ "$S" = "failed" ]; then
        [ "$S" = "failed" ] && err "Fetch: job failed"
        log "Job status: $S"
        break
    fi
    sleep 2
done

# 13. Archive with issued key.
log "Archive with issued key..."
ARCHIVE_LIST=$(curl -sf "http://localhost:8000/v1/archive/?url=http://example.com" \
    -H "X-API-Key: ${NEW_KEY}")
echo "$ARCHIVE_LIST" | python3 -c "
import sys,json
items=json.load(sys.stdin)
assert len(items)>=1, 'empty archive'
" || err "Archive empty for issued key"

ARCHIVE_ID=$(echo "$ARCHIVE_LIST" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")
ARCHIVE_CONTENT=$(curl -sf --max-time 30 "http://localhost:8000/v1/archive/${ARCHIVE_ID}" \
    -H "X-API-Key: ${NEW_KEY}")
echo "$ARCHIVE_CONTENT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('body_b64'), 'empty body_b64'" || err "Archive body empty"
log "Archive OK"

# 14. Assert usage_counter advanced for the application.
log "Usage counter check..."
USAGE_RESP=$(curl -sf "http://localhost:8000/v1/usage/applications/${APP_ID}" \
    -H "X-API-Key: ${TEST_KEY}")
echo "$USAGE_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
assert d.get('total_requests',0) >= 1, 'usage counter did not advance'
assert d.get('total_bytes',0) > 0, 'zero bytes in usage'
" || err "Usage counter check failed"
log "Usage counter OK"

# 15. Rotate the key.
log "Rotate key..."
ROTATE_RESP=$(curl -sf -X POST "http://localhost:8000/v1/keys/${KEY_ID}/rotate" \
    -H "X-API-Key: ${TEST_KEY}" \
    -H "Content-Type: application/json")
ROTATED_KEY=$(echo "$ROTATE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['raw_key'])")
ROTATED_KEY_ID=$(echo "$ROTATE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['id'])")
[ -n "$ROTATED_KEY" ] || err "Rotate: no raw_key in response"
[ "$ROTATED_KEY" != "$NEW_KEY" ] || err "Rotated key must differ from original"
[ "$ROTATED_KEY_ID" != "$KEY_ID" ] || err "Rotated key id must differ from original"
log "rotated_key_id=$ROTATED_KEY_ID prefix=${ROTATED_KEY:0:8}..."

# 16. New key works.
log "New key works..."
NEW_FETCH=$(curl -s -X POST http://localhost:8000/v1/fetch \
    -H "X-API-Key: ${ROTATED_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"url":"http://example.com","mode":"static"}')
NEW_JOB_ID=$(echo "$NEW_FETCH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('job_id',''))" 2>/dev/null)
[ -n "$NEW_JOB_ID" ] || err "New key fetch: no job_id in response"
log "New key works: job_id=$NEW_JOB_ID"

# 17. Old key still works during overlap window.
log "Old key during overlap..."
OLD_FETCH=$(curl -s -X POST http://localhost:8000/v1/fetch \
    -H "X-API-Key: ${NEW_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"url":"http://example.com","mode":"static"}')
OLD_JOB_ID=$(echo "$OLD_FETCH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('job_id',''))" 2>/dev/null)
[ -n "$OLD_JOB_ID" ] || err "Old key during overlap: no job_id — key prematurely dead"
log "Old key works during overlap: job_id=$OLD_JOB_ID"

# 18. Force old key expiry into the past.
log "Force old key expiry..."
docker compose exec -T db psql -U crawler -d crawlerdb -c \
    "UPDATE api_keys SET expires_at = NOW() - INTERVAL '1 hour' WHERE id = '${KEY_ID}'" 2>/dev/null || true

# 19. Old key must now get 401.
log "Old key after forced expiry..."
EXPIRE_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/v1/fetch \
    -H "X-API-Key: ${NEW_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"url":"http://example.com","mode":"static"}')
[ "$EXPIRE_CODE" = "401" ] || err "Old key after expiry: expected 401, got $EXPIRE_CODE"
log "Old key returns 401 after forced expiry"

# ── End operator key management workflow ───────────────────────────────────────

# 20. Cleanup
docker compose down -v 2>/dev/null || true

log "verify.sh: OK"
