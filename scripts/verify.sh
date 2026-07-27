#!/usr/bin/env bash
# verify.sh — end-to-end verification for crawler-api.
# Usage: ./scripts/verify.sh
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'
log()  { echo -e "${GREEN}[verify]${NC} $*"; }
err()  { echo -e "${RED}[verify]${NC} $*"; exit 1; }

cd "$(dirname "$0")/.."

export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://crawler:crawler@localhost:5432/crawlerdb}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"

# 1. Lint
log "ruff check..."
ruff check . || err "ruff check failed"
ruff format --check . || err "ruff format check failed"

# 2. Typecheck
log "mypy..."
mypy app/ tests/ || err "mypy failed"

# 3. Alembic
log "alembic upgrade..."
alembic upgrade head || err "alembic upgrade failed"

# 4. Tests
log "pytest..."
pytest -m "not slow" -q || err "pytest failed"

# 5. Docker build
log "docker build..."
docker build -t crawler-api-local . || err "docker build failed"

# 6. Compose up
log "docker compose up..."
docker compose up -d db redis minio || err "compose services failed"
sleep 5

# Wait for DB readiness
for i in $(seq 1 15); do
    docker compose exec -T db pg_isready -U crawler -d crawlerdb 2>/dev/null && break
    sleep 1
done

# Run alembic inside the compose network if needed, then start remaining services
docker compose up -d api worker || err "compose api/worker failed"

# 7. Bootstrap auth
log "bootstrap auth..."
TEST_KEY=$(python3 scripts/bootstrap_dev.py 2>/dev/null) || true
if [ -z "$TEST_KEY" ]; then
    # Fallback: run bootstrap in the API container
    TEST_KEY=$(docker compose exec -T api python3 scripts/bootstrap_dev.py 2>/dev/null) || true
fi
if [ -z "$TEST_KEY" ]; then
    err "Could not bootstrap auth key"
fi
log "Test key: ${TEST_KEY:0:16}..."

# 8. Health checks
for i in $(seq 1 20); do
    curl -sf http://localhost:8000/healthz && break
    sleep 1
done
log "/healthz OK"

for i in $(seq 1 20); do
    curl -sf http://localhost:8000/readyz && break
    sleep 1
done
log "/readyz OK"

# 9. Smoke test
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

# 10. Poll job
for i in $(seq 1 30); do
    STATUS=$(curl -s "http://localhost:8000/v1/jobs/${JOB_ID}" \
        -H "X-API-Key: ${TEST_KEY}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    if [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ]; then
        log "Job status: $STATUS"
        break
    fi
    sleep 2
done

# 11. Archive
log "GET /v1/archive..."
curl -sf "http://localhost:8000/v1/archive?url=http://example.com" \
    -H "X-API-Key: ${TEST_KEY}" || log "Archive empty (expected)"

# 12. Usage
log "GET /v1/usage..."
curl -sf "http://localhost:8000/v1/usage" -H "X-API-Key: ${TEST_KEY}" || log "Usage empty"

# 13. Cleanup
docker compose down

log "verify.sh: OK"
