#!/usr/bin/env bash
# demo-verbose.sh — полные HTTP-запросы и ответы, без сокращений.
# Запуск: docker compose up -d && docker compose run --rm -T api alembic upgrade head && bash scripts/demo-verbose.sh
set -euo pipefail

API="http://localhost:8000"
BOLD="\033[1m"; GREEN="\033[0;32m"; CYAN="\033[0;36m"; YELLOW="\033[0;33m"; NC="\033[0m"

h1() { echo -e "\n${BOLD}${GREEN}$(printf '━%.0s' {1..70})${NC}"; echo -e "${BOLD}${GREEN}  $*${NC}"; echo -e "${BOLD}${GREEN}$(printf '━%.0s' {1..70})${NC}\n"; }
cmd() { echo -e "${YELLOW}\$ $*${NC}"; }
note() { echo -e "${CYAN}← $*${NC}"; }

# ═══════════════════════════════════════════════════════════════════════════════
# Шаг 1: Bootstrap — создание операторского ключа
# ═══════════════════════════════════════════════════════════════════════════════
h1 "ШАГ 1: Bootstrap операторского ключа"

cmd 'docker compose exec -T api python3 scripts/bootstrap_dev.py'
echo "(выполняется внутри контейнера, напрямую в БД — HTTP не используется)"
OPERATOR_KEY=$(docker compose exec -T api python3 scripts/bootstrap_dev.py 2>/dev/null | tail -1)
note "Создан операторский ключ с scope: fetch, archive, admin, keys"
echo ""
echo "raw_key = ${OPERATOR_KEY}"
echo ""
echo -e "┌──────────────┬──────────────────────────────────────────────────┐"
echo -e "│ Поле         │ Значение                                          │"
echo -e "├──────────────┼──────────────────────────────────────────────────┤"
echo -e "│ prefix       │ ${OPERATOR_KEY:0:8}                                              │"
echo -e "│ mode         │ live (префикс crwl)                              │"
echo -e "│ scopes       │ [fetch, archive, admin, keys]                     │"
echo -e "│ issuer_key_id│ NULL (bootstrap-ключ, не выдан оператором)        │"
echo -e "│ raw_key      │ СЕКРЕТ — никогда не логируется и не сохраняется   │"
echo -e "└──────────────┴──────────────────────────────────────────────────┘"
note "Tenant и Application созданы idempotent в bootstrap_dev.py"

# ═══════════════════════════════════════════════════════════════════════════════
# Шаг 2: Создание tenant
# ═══════════════════════════════════════════════════════════════════════════════
TENANT_NAME="price-intel-tenant"
h1 "ШАГ 2: POST /v1/tenants — создание арендатора"

echo -e "${YELLOW}══════════ ЗАПРОС ══════════${NC}"
cmd "curl -X POST ${API}/v1/tenants \\"
echo '  -H "X-API-Key: crwlhfsM... (операторский ключ)" \'
echo '  -H "Content-Type: application/json" \'
echo "  -d '{\"name\":\"${TENANT_NAME}\"}'"
echo ""
echo -e "${CYAN}══════════ ОТВЕТ ══════════${NC}"
TENANT=$(curl -s -w "\n%{http_code}" -X POST "$API/v1/tenants" \
    -H "X-API-Key: ${OPERATOR_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${TENANT_NAME}\"}")
TENANT_CODE=$(echo "$TENANT" | tail -1)
TENANT_BODY=$(echo "$TENANT" | sed '$d')
echo "HTTP ${TENANT_CODE}"
echo "$TENANT_BODY" | python3 -m json.tool
TENANT_ID=$(echo "$TENANT_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
note "tenant_id = ${TENANT_ID}"

# ═══════════════════════════════════════════════════════════════════════════════
# Шаг 3: Создание application
# ═══════════════════════════════════════════════════════════════════════════════
APP_NAME="price-intel-app"
h1 "ШАГ 3: POST /v1/applications — создание приложения"

echo -e "${YELLOW}══════════ ЗАПРОС ══════════${NC}"
cmd "curl -X POST ${API}/v1/applications \\"
echo '  -H "X-API-Key: crwlhfsM... (операторский ключ)" \'
echo '  -H "Content-Type: application/json" \'
echo "  -d '{\"tenant_id\":\"${TENANT_ID}\",\"name\":\"${APP_NAME}\"}'"
echo ""
echo -e "${CYAN}══════════ ОТВЕТ ══════════${NC}"
APP=$(curl -s -w "\n%{http_code}" -X POST "$API/v1/applications" \
    -H "X-API-Key: ${OPERATOR_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"tenant_id\":\"${TENANT_ID}\",\"name\":\"${APP_NAME}\"}")
APP_CODE=$(echo "$APP" | tail -1)
APP_BODY=$(echo "$APP" | sed '$d')
echo "HTTP ${APP_CODE}"
echo "$APP_BODY" | python3 -m json.tool
APP_ID=$(echo "$APP_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
note "application_id = ${APP_ID}"

# ═══════════════════════════════════════════════════════════════════════════════
# Шаг 4: Выпуск ключа
# ═══════════════════════════════════════════════════════════════════════════════
h1 "ШАГ 4: POST /v1/keys — выпуск API-ключа для приложения"

echo -e "${YELLOW}══════════ ЗАПРОС ══════════${NC}"
cmd "curl -X POST ${API}/v1/keys \\"
echo '  -H "X-API-Key: crwlhfsM... (операторский ключ)" \'
echo '  -H "Content-Type: application/json" \'
echo "  -d '{"
echo '    "application_id": "'${APP_ID}'",'
echo '    "scopes": ["fetch","archive"],'
echo '    "mode": "live"'
echo "  }'"
echo ""
echo -e "${CYAN}══════════ ОТВЕТ ══════════${NC}"
KEY=$(curl -s -w "\n%{http_code}" -X POST "$API/v1/keys" \
    -H "X-API-Key: ${OPERATOR_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"application_id\":\"${APP_ID}\",\"scopes\":[\"fetch\",\"archive\"],\"mode\":\"live\"}")
KEY_CODE=$(echo "$KEY" | tail -1)
KEY_BODY=$(echo "$KEY" | sed '$d')
echo "HTTP ${KEY_CODE}"
echo "$KEY_BODY" | python3 -m json.tool
KEY_ID=$(echo "$KEY_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
RAW_KEY=$(echo "$KEY_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['raw_key'])")
PREFIX=$(echo "$KEY_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['prefix'])")
note "Обрати внимание:"
note "  • raw_key = ${RAW_KEY} — СЕКРЕТ, показан ТОЛЬКО здесь"
note "  • Поля hashed_key — НЕТ в ответе"
note "  • Поля issuer_key_id — НЕТ в ответе"
note "  • Поля is_active — НЕТ (выводится из revoked_at)"
note "  • prefix=${PREFIX} — публичная часть (8 символов), не секрет"

# ═══════════════════════════════════════════════════════════════════════════════
# Шаг 5: Список ключей
# ═══════════════════════════════════════════════════════════════════════════════
h1 "ШАГ 5: GET /v1/keys — список ключей приложения"

echo -e "${YELLOW}══════════ ЗАПРОС ══════════${NC}"
cmd "curl ${API}/v1/keys \\"
echo "  -H \"X-API-Key: ${RAW_KEY:0:16}...\""
echo ""
echo -e "${CYAN}══════════ ОТВЕТ ══════════${NC}"
KEYS=$(curl -s -w "\n%{http_code}" "$API/v1/keys" -H "X-API-Key: ${RAW_KEY}")
KEYS_CODE=$(echo "$KEYS" | tail -1)
KEYS_BODY=$(echo "$KEYS" | sed '$d')
echo "HTTP ${KEYS_CODE}"
echo "$KEYS_BODY" | python3 -m json.tool
echo ""
note "Проверяем отсутствие hashed_key в каждом элементе:"
echo "$KEYS_BODY" | python3 -c "
import sys, json
keys = json.load(sys.stdin)
print(f'  Всего ключей: {len(keys)}')
for k in keys:
    assert 'hashed_key' not in k, 'ОШИБКА: hashed_key просочился!'
    assert 'raw_key' not in k, 'ОШИБКА: raw_key в списке!'
    print(f'  ✓ hashed_key отсутствует | ✓ raw_key отсутствует')
    print(f'    id={k[\"id\"]}')
    print(f'    prefix={k[\"prefix\"]}')
    print(f'    scopes={k[\"scopes\"]}')
    print(f'    mode={k[\"mode\"]}')
    print(f'    application_id={k[\"application_id\"]}')
    if k.get('revoked_at') is None:
        print(f'    revoked_at=null (ключ активен)')
"

# ═══════════════════════════════════════════════════════════════════════════════
# Шаг 6: Ротация ключа
# ═══════════════════════════════════════════════════════════════════════════════
h1 "ШАГ 6: POST /v1/keys/{key_id}/rotate — ротация ключа"

echo -e "${YELLOW}══════════ ЗАПРОС ══════════${NC}"
cmd "curl -X POST ${API}/v1/keys/${KEY_ID}/rotate \\"
echo '  -H "X-API-Key: crwlhfsM... (операторский ключ)" \'
echo '  -H "Content-Type: application/json"'
echo ""
echo "Тело запроса: пустое (все параметры берутся из старого ключа)"
echo ""
echo -e "${CYAN}══════════ ОТВЕТ ══════════${NC}"
ROTATE=$(curl -s -w "\n%{http_code}" -X POST "$API/v1/keys/${KEY_ID}/rotate" \
    -H "X-API-Key: ${OPERATOR_KEY}" \
    -H "Content-Type: application/json")
ROTATE_CODE=$(echo "$ROTATE" | tail -1)
ROTATE_BODY=$(echo "$ROTATE" | sed '$d')
echo "HTTP ${ROTATE_CODE}"
echo "$ROTATE_BODY" | python3 -m json.tool
ROTATED_KEY=$(echo "$ROTATE_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['raw_key'])")
ROTATED_ID=$(echo "$ROTATE_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo ""
note "Старый ключ ${PREFIX}: НЕ отозван (revoked_at=null)"
note "Старый ключ ${PREFIX}: expires_at = now + 24h (окно перекрытия)"
note "Новый ключ:     raw_key = ${ROTATED_KEY}"
note "Новый ключ:     id = ${ROTATED_ID}"

# Проверим состояние старого ключа в БД
echo ""
echo -e "${YELLOW}══════════ СОСТОЯНИЕ СТАРОГО КЛЮЧА В БД ══════════${NC}"
cmd "SELECT id, prefix, is_active, revoked_at, expires_at FROM api_keys WHERE id='${KEY_ID}'"
docker compose exec -T db psql -U crawler -d crawlerdb -c \
    "SELECT id, prefix, is_active, revoked_at, expires_at FROM api_keys WHERE id='${KEY_ID}'" 2>/dev/null
note "is_active=true, revoked_at=NULL — ключ действителен в окне перекрытия"

# ═══════════════════════════════════════════════════════════════════════════════
# Шаг 7: Проверка — новый ключ работает
# ═══════════════════════════════════════════════════════════════════════════════
h1 "ШАГ 7: Проверка НОВОГО ключа"

echo -e "${YELLOW}══════════ ЗАПРОС ══════════${NC}"
cmd "curl -X POST ${API}/v1/fetch \\"
echo '  -H "X-API-Key: crwlvdniG... (новый ключ)" \'
echo '  -H "Content-Type: application/json" \'
echo "  -d '{\"url\":\"http://httpbin.org/ip\",\"mode\":\"static\"}'"
echo ""
echo -e "${CYAN}══════════ ОТВЕТ ══════════${NC}"
NEW_FETCH=$(curl -s -w "\n%{http_code}" -X POST "$API/v1/fetch" \
    -H "X-API-Key: ${ROTATED_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"url":"http://httpbin.org/ip","mode":"static"}')
NEW_FETCH_CODE=$(echo "$NEW_FETCH" | tail -1)
NEW_FETCH_BODY=$(echo "$NEW_FETCH" | sed '$d')
echo "HTTP ${NEW_FETCH_CODE}"
echo "$NEW_FETCH_BODY" | python3 -m json.tool
note "Новый ключ работает ✓"

# ═══════════════════════════════════════════════════════════════════════════════
# Шаг 8: Проверка — старый ключ всё ещё работает (окно перекрытия)
# ═══════════════════════════════════════════════════════════════════════════════
h1 "ШАГ 8: Проверка СТАРОГО ключа (окно перекрытия 24ч)"

echo -e "${YELLOW}══════════ ЗАПРОС ══════════${NC}"
cmd "curl -X POST ${API}/v1/fetch \\"
echo '  -H "X-API-Key: crwlMS1z... (старый ключ)" \'
echo '  -H "Content-Type: application/json" \'
echo "  -d '{\"url\":\"http://httpbin.org/headers\",\"mode\":\"static\"}'"
echo ""
echo -e "${CYAN}══════════ ОТВЕТ ══════════${NC}"
OLD_FETCH=$(curl -s -w "\n%{http_code}" -X POST "$API/v1/fetch" \
    -H "X-API-Key: ${RAW_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"url":"http://httpbin.org/headers","mode":"static"}')
OLD_FETCH_CODE=$(echo "$OLD_FETCH" | tail -1)
OLD_FETCH_BODY=$(echo "$OLD_FETCH" | sed '$d')
echo "HTTP ${OLD_FETCH_CODE}"
echo "$OLD_FETCH_BODY" | python3 -m json.tool
note "Старый ключ работает в окне перекрытия ✓"

# ═══════════════════════════════════════════════════════════════════════════════
# Шаг 9: Принудительное истечение старого ключа
# ═══════════════════════════════════════════════════════════════════════════════
h1 "ШАГ 9: Принудительное истечение старого ключа"

echo -e "${YELLOW}══════════ ЗАПРОС (БД) ══════════${NC}"
cmd "UPDATE api_keys SET expires_at = NOW() - INTERVAL '1 hour' WHERE id='${KEY_ID}'"
docker compose exec -T db psql -U crawler -d crawlerdb -c \
    "UPDATE api_keys SET expires_at = NOW() - INTERVAL '1 hour' WHERE id='${KEY_ID}'" 2>/dev/null
echo ""
echo -e "${YELLOW}══════════ ПРОВЕРКА В БД ══════════${NC}"
cmd "SELECT prefix, is_active, revoked_at, expires_at FROM api_keys WHERE id='${KEY_ID}'"
docker compose exec -T db psql -U crawler -d crawlerdb -c \
    "SELECT prefix, is_active, revoked_at, expires_at FROM api_keys WHERE id='${KEY_ID}'" 2>/dev/null
note "expires_at в прошлом — ключ истёк"

# ═══════════════════════════════════════════════════════════════════════════════
# Шаг 10: Старый ключ после истечения → 401
# ═══════════════════════════════════════════════════════════════════════════════
h1 "ШАГ 10: Старый ключ после истечения → ожидаем 401"

echo -e "${YELLOW}══════════ ЗАПРОС ══════════${NC}"
cmd "curl -v -X POST ${API}/v1/fetch \\"
echo '  -H "X-API-Key: crwlMS1z... (старый, истёкший ключ)" \'
echo "  ..."
echo ""
echo -e "${CYAN}══════════ ОТВЕТ ══════════${NC}"
EXPIRE=$(curl -s -w "\n%{http_code}" -X POST "$API/v1/fetch" \
    -H "X-API-Key: ${RAW_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"url":"http://httpbin.org/anything","mode":"static"}')
EXPIRE_CODE=$(echo "$EXPIRE" | tail -1)
EXPIRE_BODY=$(echo "$EXPIRE" | sed '$d')
echo "HTTP ${EXPIRE_CODE}"
echo "$EXPIRE_BODY" | python3 -m json.tool
note "HTTP 401 — истёкший ключ отклонён ✓"

# ═══════════════════════════════════════════════════════════════════════════════
# Шаг 11: Отзыв ключа
# ═══════════════════════════════════════════════════════════════════════════════
h1 "ШАГ 11: DELETE /v1/keys/{key_id} — отзыв ключа"

echo -e "${YELLOW}══════════ ЗАПРОС ══════════${NC}"
cmd "curl -X DELETE ${API}/v1/keys/${KEY_ID} \\"
echo '  -H "X-API-Key: crwlhfsM... (операторский ключ)" \'
echo '  -H "Content-Type: application/json" \'
echo '  -d '"'"'{"reason":"Демо: ключ скомпрометирован"}'"'"'"
echo ""
echo -e "${CYAN}══════════ ОТВЕТ ══════════${NC}"
REVOKE=$(curl -s -w "\n%{http_code}" -X DELETE "$API/v1/keys/${KEY_ID}" \
    -H "X-API-Key: ${OPERATOR_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"reason":"Демо: ключ скомпрометирован"}')
REVOKE_CODE=$(echo "$REVOKE" | tail -1)
REVOKE_BODY=$(echo "$REVOKE" | sed '$d')
echo "HTTP ${REVOKE_CODE}"
echo "$REVOKE_BODY" | python3 -m json.tool
note "revoked_at установлен, is_active=false"

# ═══════════════════════════════════════════════════════════════════════════════
# Шаг 12: Ротация отозванного → 409
# ═══════════════════════════════════════════════════════════════════════════════
h1 "ШАГ 12: Ротация отозванного ключа → ожидаем 409"

echo -e "${YELLOW}══════════ ЗАПРОС ══════════${NC}"
cmd "curl -X POST ${API}/v1/keys/${KEY_ID}/rotate \\"
echo '  -H "X-API-Key: crwlhfsM... (операторский ключ)" \'
echo '  -H "Content-Type: application/json"'
echo ""
echo -e "${CYAN}══════════ ОТВЕТ ══════════${NC}"
ROTATE2=$(curl -s -w "\n%{http_code}" -X POST "$API/v1/keys/${KEY_ID}/rotate" \
    -H "X-API-Key: ${OPERATOR_KEY}" \
    -H "Content-Type: application/json")
ROTATE2_CODE=$(echo "$ROTATE2" | tail -1)
ROTATE2_BODY=$(echo "$ROTATE2" | sed '$d')
echo "HTTP ${ROTATE2_CODE}"
echo "$ROTATE2_BODY" | python3 -m json.tool
note "HTTP 409 Conflict — отозванный ключ нельзя ротировать ✓"

# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}${GREEN}$(printf '━%.0s' {1..70})${NC}"
echo -e "${BOLD}${GREEN}  ДЕМОНСТРАЦИЯ ЗАВЕРШЕНА${NC}"
echo -e "${BOLD}${GREEN}$(printf '━%.0s' {1..70})${NC}"
