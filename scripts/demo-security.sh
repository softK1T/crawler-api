#!/usr/bin/env bash
# demo-security.sh — демонстрация защиты: что НЕЛЬЗЯ сделать.
# Запуск: docker compose up -d && sleep 3 && bash scripts/demo-security.sh
set -euo pipefail

API="http://localhost:8000"
BOLD="\033[1m"; RED="\033[0;31m"; GREEN="\033[0;32m"; CYAN="\033[0;36m"; NC="\033[0m"

say()  { echo -e "\n${BOLD}${GREEN}═══ $* ═══${NC}"; }
info() { echo -e "${CYAN}→${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; }

# ── Подготовка ────────────────────────────────────────────────────────────────
say "Подготовка: создаём оператора и два приложения"
OPERATOR_KEY=$(docker compose exec -T api python3 scripts/bootstrap_dev.py 2>/dev/null | tail -1)

# Tenant + App1 + App2
T1=$(curl -sf -X POST "$API/v1/tenants" -H "X-API-Key: ${OPERATOR_KEY}" -H "Content-Type: application/json" -d '{"name":"demo-tenant"}')
T1_ID=$(echo "$T1" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

A1=$(curl -sf -X POST "$API/v1/applications" -H "X-API-Key: ${OPERATOR_KEY}" -H "Content-Type: application/json" -d "{\"tenant_id\":\"${T1_ID}\",\"name\":\"app-alpha\"}")
A1_ID=$(echo "$A1" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

A2=$(curl -sf -X POST "$API/v1/applications" -H "X-API-Key: ${OPERATOR_KEY}" -H "Content-Type: application/json" -d "{\"tenant_id\":\"${T1_ID}\",\"name\":\"app-beta\"}")
A2_ID=$(echo "$A2" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

info "app-alpha id=$A1_ID"
info "app-beta  id=$A2_ID"

# Создаём ключ только с fetch (без keys, без admin)
FETCH_KEY=$(curl -sf -X POST "$API/v1/keys" -H "X-API-Key: ${OPERATOR_KEY}" -H "Content-Type: application/json" \
    -d "{\"application_id\":\"${A1_ID}\",\"scopes\":[\"fetch\"],\"mode\":\"live\"}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['raw_key'])")
info "Ключ app-alpha (только fetch): ${FETCH_KEY:0:16}..."

# Создаём ключ с keys (но БЕЗ admin)
KEYS_KEY=$(curl -sf -X POST "$API/v1/keys" -H "X-API-Key: ${OPERATOR_KEY}" -H "Content-Type: application/json" \
    -d "{\"application_id\":\"${A1_ID}\",\"scopes\":[\"keys\",\"fetch\"],\"mode\":\"live\"}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['raw_key'])")
info "Ключ app-alpha (keys+fetch, без admin): ${KEYS_KEY:0:16}..."

# ── Тест 1: fetch-ключ не может создавать другие ключи ───────────────────────
say "Тест 1: fetch-ключ пытается создать ключ → 403"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/v1/keys" \
    -H "X-API-Key: ${FETCH_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"application_id\":\"${A1_ID}\",\"scopes\":[\"fetch\"],\"mode\":\"live\"}")
[ "$CODE" = "403" ] && info "HTTP 403 — у fetch-ключа нет scope keys ✓" || fail "HTTP $CODE"

# ── Тест 2: keys-без-admin не может выдать scope admin ────────────────────────
say "Тест 2: keys-ключ пытается выдать admin → 403 (эскалация привилегий)"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/v1/keys" \
    -H "X-API-Key: ${KEYS_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"application_id\":\"${A1_ID}\",\"scopes\":[\"admin\"],\"mode\":\"live\"}")
[ "$CODE" = "403" ] && info "HTTP 403 — нельзя выдать scope, которого нет у тебя ✓" || fail "HTTP $CODE"

# ── Тест 3: keys-без-admin не может выдать scope keys другому ─────────────────
say "Тест 3: keys-ключ без admin пытается выдать keys → 403"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/v1/keys" \
    -H "X-API-Key: ${KEYS_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"application_id\":\"${A1_ID}\",\"scopes\":[\"keys\"],\"mode\":\"live\"}")
[ "$CODE" = "403" ] && info "HTTP 403 — для выдачи keys нужен admin ✓" || fail "HTTP $CODE"

# ── Тест 4: keys-без-admin не может создать ключ для чужого приложения ────────
say "Тест 4: keys-ключ пытается создать ключ для app-beta → 403 (cross-tenant)"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/v1/keys" \
    -H "X-API-Key: ${KEYS_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"application_id\":\"${A2_ID}\",\"scopes\":[\"fetch\"],\"mode\":\"live\"}")
[ "$CODE" = "403" ] && info "HTTP 403 — keys-без-admin привязан к своему приложению ✓" || fail "HTTP $CODE"

# ── Тест 5: Оператор (admin+keys) может создать ключ для любого приложения ────
say "Тест 5: Оператор с admin+keys создаёт ключ для app-beta → 201"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/v1/keys" \
    -H "X-API-Key: ${OPERATOR_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"application_id\":\"${A2_ID}\",\"scopes\":[\"fetch\",\"archive\"],\"mode\":\"live\"}")
[ "$CODE" = "201" ] && info "HTTP 201 — оператор может работать с любым приложением ✓" || fail "HTTP $CODE"

# ── Тест 6: Без ключа вообще → 401 ────────────────────────────────────────────
say "Тест 6: Запрос без API-ключа → 401"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/v1/fetch" \
    -H "Content-Type: application/json" \
    -d '{"url":"http://example.com","mode":"static"}')
[ "$CODE" = "401" ] && info "HTTP 401 — анонимные запросы отклонены ✓" || fail "HTTP $CODE"

# ── Тест 7: Нельзя зарегистрироваться самостоятельно ──────────────────────────
say "Тест 7: Попытка саморегистрации → 404 (нет такого эндпоинта)"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/v1/register" \
    -H "Content-Type: application/json" \
    -d '{"email":"hacker@evil.com"}' 2>/dev/null)
[ "$CODE" = "404" ] && info "HTTP 404 — эндпоинта /v1/register не существует ✓" || info "HTTP $CODE"

echo ""
echo -e "${BOLD}${GREEN}═══ Проверка защиты завершена ═══${NC}"
echo ""
echo "Что мы проверили:"
echo "  1. fetch-ключ не может управлять ключами (403)"
echo "  2. Нельзя выдать scope, которого нет у тебя (403)"
echo "  3. Для выдачи scope keys нужен scope admin (403)"
echo "  4. keys-без-admin привязан к своему приложению (403)"
echo "  5. Оператор с admin+keys работает кросс-приложений (201)"
echo "  6. Без ключа — 401"
echo "  7. Саморегистрации нет — /v1/register не существует (404)"
