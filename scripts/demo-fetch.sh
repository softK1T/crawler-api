#!/usr/bin/env bash
set -euo pipefail
API="http://localhost:8000"

OPERATOR_KEY=$(docker compose exec -T api python3 scripts/bootstrap_dev.py 2>/dev/null | tail -1)
APP_ID=$(curl -sf "$API/v1/applications" -H "X-API-Key: ${OPERATOR_KEY}" | python3 -c "import sys,json; print(json.load(sys.stdin)['items'][0]['id'])")
RAW_KEY=$(curl -sf -X POST "$API/v1/keys" -H "X-API-Key: ${OPERATOR_KEY}" -H "Content-Type: application/json" -d "{\"application_id\":\"${APP_ID}\",\"scopes\":[\"fetch\",\"archive\"],\"mode\":\"live\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)['raw_key'])")

echo "══════════ 1. POST /v1/fetch (создание задачи) ══════════"
echo ""
echo "\$ curl -X POST $API/v1/fetch -H 'X-API-Key: crwl...' -d '{\"url\":\"http://example.com\",\"mode\":\"static\"}'"
echo ""
JOB=$(curl -s -X POST "$API/v1/fetch" -H "X-API-Key: ${RAW_KEY}" -H "Content-Type: application/json" -d '{"url":"http://example.com","mode":"static"}')
echo "$JOB" | python3 -m json.tool
JOB_ID=$(echo "$JOB" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")

echo ""
echo "══════════ 2. GET /v1/jobs/{id} (ожидание выполнения) ══════════"
for i in $(seq 1 15); do
    ST=$(curl -sf "$API/v1/jobs/${JOB_ID}" -H "X-API-Key: ${RAW_KEY}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
    echo "  #$i: $ST"
    if [ "$ST" = "completed" ]; then
        echo ""
        echo "══════════ РЕЗУЛЬТАТ ВЫПОЛНЕНИЯ ══════════"
        curl -s "$API/v1/jobs/${JOB_ID}" -H "X-API-Key: ${RAW_KEY}" | python3 -m json.tool
        break
    fi
    sleep 2
done

echo ""
echo "══════════ 3. GET /v1/archive/ (список архивных записей) ══════════"
ARCHIVE=$(curl -s "$API/v1/archive/?url=http://example.com" -H "X-API-Key: ${RAW_KEY}")
echo "$ARCHIVE" | python3 -m json.tool
AID=$(echo "$ARCHIVE" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")

echo ""
echo "══════════ 4. GET /v1/archive/{id} (содержимое ответа) ══════════"
CONTENT=$(curl -s --max-time 30 "$API/v1/archive/${AID}" -H "X-API-Key: ${RAW_KEY}")
echo "$CONTENT" | python3 -c "
import sys,json,base64
d=json.load(sys.stdin)
body=base64.b64decode(d['body_b64']).decode('utf-8',errors='replace')
print(f'  url:          {d[\"url\"]}')
print(f'  status_code:  {d[\"status_code\"]}')
print(f'  content_type: {d[\"content_type\"]}')
print(f'')
print(f'  === ТЕЛО ОТВЕТА (example.com) ===')
print(body)
"
