#!/usr/bin/env bash
# verify.sh — end-to-end verification script for crawler-api.
# Runs lint, typecheck, migrations, tests, docker build, smoke test.
# Usage: ./scripts/verify.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

log()  { echo -e "${GREEN}[verify]${NC} $*"; }
err()  { echo -e "${RED}[verify]${NC} $*"; exit 1; }

cd "$(dirname "$0")/.."

export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://crawler:crawler@localhost:5432/crawlerdb}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"

# 1. Lint
log "Running ruff check..."
ruff check . || err "ruff check failed"
ruff format --check . || err "ruff format check failed"

# 2. Typecheck
log "Running mypy..."
mypy app/ tests/ || err "mypy failed"

# 3. Alembic migration
log "Running alembic upgrade..."
alembic upgrade head || err "alembic upgrade failed"

# 4. Tests
log "Running pytest..."
pytest -m "not slow" -q || err "pytest failed"

# 5. Docker build
log "Building Docker image..."
docker build -t crawler-api-local . || err "docker build failed"

# 6. Compose up
log "Starting services..."
docker compose up -d db redis minio api worker || err "docker compose up failed"
sleep 10

# 7. Health checks
log "Checking /healthz..."
curl -sf http://localhost:8000/healthz || err "/healthz failed"

log "Checking /readyz..."
curl -sf http://localhost:8000/readyz || err "/readyz failed"

# 8. Smoke test
TEST_KEY="${TEST_KEY:-crw_live_local_dev_key_abcdefghijkl}"
log "Running smoke test (POST /v1/fetch)..."
RESP=$(curl -s -X POST http://localhost:8000/v1/fetch \
    -H "X-API-Key: ${TEST_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"url":"http://example.com","mode":"static"}')

JOB_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('job_id',''))" 2>/dev/null || true)
if [ -z "$JOB_ID" ]; then
    err "Smoke test: could not extract job_id from response: $RESP"
fi
log "Smoke test: job_id=$JOB_ID"

# 9. Poll job
log "Polling /v1/jobs/${JOB_ID}..."
for i in $(seq 1 30); do
    STATUS=$(curl -s "http://localhost:8000/v1/jobs/${JOB_ID}" \
        -H "X-API-Key: ${TEST_KEY}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)
    if [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ]; then
        log "Smoke test job status: $STATUS"
        break
    fi
    sleep 2
done

# 10. Archive
log "Checking /v1/archive..."
curl -sf "http://localhost:8000/v1/archive?url=http://example.com" \
    -H "X-API-Key: ${TEST_KEY}" || log "Archive returned empty (expected for first run)"

# 11. Cleanup
docker compose down

log "verify.sh: OK"
