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

# 9. Smoke test — POST /v1/fetch
log "POST /v1/fetch..."
RESP=$(curl -s -X POST http://localhost:8000/v1/fetch \
    -H "X-API-Key: ${TEST_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"url":"http://example.com","mode":"static"}')

JOB_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('job_id',''))" 2>/dev/null)
if [ -z "$JOB_ID" ]; then
    err "Smoke: no job_id in response: $RESP"
fi
log "job_id=$JOB_ID"

# 10. Poll job — wait for completion (bounded).
log "Poll job..."
for i in $(seq 1 30); do
    S="$(curl -s "http://localhost:8000/v1/jobs/${JOB_ID}" \
        -H "X-API-Key: ${TEST_KEY}" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('status',''))
" 2>/dev/null)"
    if [ "$S" = "completed" ] || [ "$S" = "failed" ]; then
        log "Job status: $S"
        [ "$S" = "failed" ] && err "Smoke: job failed"
        break
    fi
    sleep 2
done

# 11. Archive — list and get content.
log "GET /v1/archive/..."
ARCHIVE_LIST=$(curl -sf "http://localhost:8000/v1/archive/?url=http://example.com" \
    -H "X-API-Key: ${TEST_KEY}")
echo "$ARCHIVE_LIST" | python3 -c "import sys,json; items=json.load(sys.stdin); assert len(items)>=1, 'empty archive'" || err "Archive empty"

REQUEST_ID=$(echo "$ARCHIVE_LIST" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])" 2>/dev/null)
log "GET /v1/archive/${REQUEST_ID}..."
ARCHIVE_CONTENT=$(curl -sf "http://localhost:8000/v1/archive/${REQUEST_ID}" \
    -H "X-API-Key: ${TEST_KEY}")
echo "$ARCHIVE_CONTENT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('body_b64'), 'empty body_b64'" || err "Archive body empty"
log "Archive OK"

# 12. Usage
log "GET /v1/usage/..."
USAGE=$(curl -sf "http://localhost:8000/v1/usage/" -H "X-API-Key: ${TEST_KEY}")
echo "$USAGE"
echo "$USAGE" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('total_requests',0)>=1, 'no requests'; assert d.get('total_bytes',0)>0, 'zero bytes'" || err "Usage empty"

# 13. Cleanup
docker compose down -v 2>/dev/null || true

log "verify.sh: OK"
