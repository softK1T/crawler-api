#!/usr/bin/env bash
# demo.sh — пошаговая демонстрация операторского управления API-ключами.
# Запуск: docker compose up -d && sleep 3 && bash scripts/demo.sh
set -euo pipefail

API="http://localhost:8000"
BOLD="\033[1m"; GREEN="\033[0;32m"; CYAN="\033[0;36m"; NC="\033[0m"

say()  { echo -e "\n${BOLD}${GREEN}═══ $* ═══${NC}"; }
info() { echo -e "${CYAN}→${NC} $*"; }

# ── Шаг 1: Получаем операторский ключ через bootstrap ────────────────────────
say "Шаг 1: Создание операторского ключа (bootstrap)"
info "Запуск scripts/bootstrap_dev.py внутри контейнера..."
OPERATOR_KEY=$(docker compose exec -T api python3 scripts/bootstrap_dev.py 2>/dev/null | tail -1)
info "Операторский ключ: ${OPERATOR_KEY:0:16}... (полный не показываем)"
info "Этот ключ имеет все scope: fetch, archive, admin, keys"
info "Он создан однократно оператором вручную, не через самообслуживание."

# ── Шаг 2: Создаём tenant ─────────────────────────────────────────────────────
say "Шаг 2: Создание tenant (арендатора)"
info "POST /v1/tenants — требует scope admin"
TENANT=$(curl -sf -X POST "$API/v1/tenants" \
    -H "X-API-Key: ${OPERATOR_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"name":"ООО Ромашка"}')
TENANT_ID=$(echo "$TENANT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['id'])")
echo "$TENANT" | python3 -m json.tool
info "tenant_id=$TENANT_ID — арендатор создан."

# ── Шаг 3: Создаём application внутри tenant ──────────────────────────────────
say "Шаг 3: Создание application (приложения) внутри tenant"
info "POST /v1/applications — требует scope admin"
APP=$(curl -sf -X POST "$API/v1/applications" \
    -H "X-API-Key: ${OPERATOR_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"tenant_id\":\"${TENANT_ID}\",\"name\":\"price-intel-v2\"}")
APP_ID=$(echo "$APP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['id'])")
echo "$APP" | python3 -m json.tool
info "application_id=$APP_ID — приложение создано."

# ── Шаг 4: Выпускаем API-ключ для приложения ──────────────────────────────────
say "Шаг 4: Выпуск API-ключа для приложения"
info "POST /v1/keys — требует scope keys"
info "Запрашиваем scope: fetch, archive"
KEY_RESP=$(curl -sf -X POST "$API/v1/keys" \
    -H "X-API-Key: ${OPERATOR_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"application_id\":\"${APP_ID}\",\"scopes\":[\"fetch\",\"archive\"],\"mode\":\"live\"}")
KEY_ID=$(echo "$KEY_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['id'])")
RAW_KEY=$(echo "$KEY_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['raw_key'])")
PREFIX=$(echo "$KEY_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['prefix'])")
info "key_id=$KEY_ID"
info "prefix=$PREFIX (первые 8 символов — публичная часть)"
info "raw_key=${RAW_KEY:0:16}... (секрет — показан только при создании!)"
info "Обрати внимание: в ответе нет hashed_key и issuer_key_id — они скрыты."

# ── Шаг 5: Проверяем список ключей ────────────────────────────────────────────
say "Шаг 5: Список ключей приложения"
info "GET /v1/keys — любой аутентифицированный ключ видит ключи своего приложения"
KEYS=$(curl -sf "$API/v1/keys" -H "X-API-Key: ${RAW_KEY}")
echo "$KEYS" | python3 -c "
import sys, json
keys = json.load(sys.stdin)
print(f'  Всего ключей: {len(keys)}')
for k in keys:
    print(f'  - id={k[\"id\"]} prefix={k[\"prefix\"]} scopes={k[\"scopes\"]} mode={k[\"mode\"]}')
    assert 'hashed_key' not in k, 'ОШИБКА: hashed_key просочился в ответ!'
print('  hashed_key отсутствует — защита работает.')
"

# ── Шаг 6: Используем ключ для fetch ──────────────────────────────────────────
say "Шаг 6: Использование ключа — отправка запроса на crawl"
info "POST /v1/fetch — требует scope fetch"
info "Отправляем запрос новым ключом (не операторским!)"
JOB=$(curl -sf -X POST "$API/v1/fetch" \
    -H "X-API-Key: ${RAW_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"url":"http://httpbin.org/get","mode":"static"}')
JOB_ID=$(echo "$JOB" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['job_id'])")
echo "$JOB" | python3 -m json.tool
info "job_id=$JOB_ID — задача поставлена в очередь."

# ── Шаг 7: Ждём выполнения ────────────────────────────────────────────────────
say "Шаг 7: Ожидание выполнения задачи"
for i in $(seq 1 15); do
    STATUS=$(curl -sf "$API/v1/jobs/${JOB_ID}" -H "X-API-Key: ${RAW_KEY}" \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
    info "  попытка $i: status=$STATUS"
    if [ "$STATUS" = "completed" ]; then break; fi
    sleep 1
done
[ "$STATUS" = "completed" ] && info "Задача выполнена успешно!" || info "Завершилась со статусом: $STATUS"

# ── Шаг 8: Ротация ключа ──────────────────────────────────────────────────────
say "Шаг 8: Ротация (замена) ключа"
info "POST /v1/keys/{key_id}/rotate — требует scope keys"
info "Старый ключ остаётся рабочим ещё 24 часа (окно перекрытия)"
ROTATE=$(curl -sf -X POST "$API/v1/keys/${KEY_ID}/rotate" \
    -H "X-API-Key: ${OPERATOR_KEY}" \
    -H "Content-Type: application/json")
ROTATED_KEY=$(echo "$ROTATE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['raw_key'])")
ROTATED_ID=$(echo "$ROTATE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['id'])")
info "Новый key_id=$ROTATED_ID"
info "Новый raw_key=${ROTATED_KEY:0:16}..."
info "Старый ключ ($PREFIX) всё ещё действителен — окно 24ч для замены на клиенте."

# ── Шаг 9: Проверяем — новый ключ работает ─────────────────────────────────────
say "Шаг 9: Проверка нового ключа"
info "Отправляем fetch новым ключом..."
NEW_JOB=$(curl -sf -X POST "$API/v1/fetch" \
    -H "X-API-Key: ${ROTATED_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"url":"http://httpbin.org/ip","mode":"static"}')
NEW_JOB_ID=$(echo "$NEW_JOB" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['job_id'])")
info "Новый ключ работает: job_id=$NEW_JOB_ID ✓"

# ── Шаг 10: Проверяем — старый ключ всё ещё работает ───────────────────────────
say "Шаг 10: Проверка старого ключа в окне перекрытия"
info "Старый ключ должен работать в течение 24ч после ротации..."
OLD_JOB=$(curl -sf -X POST "$API/v1/fetch" \
    -H "X-API-Key: ${RAW_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"url":"http://httpbin.org/headers","mode":"static"}')
OLD_JOB_ID=$(echo "$OLD_JOB" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['job_id'])")
info "Старый ключ работает: job_id=$OLD_JOB_ID ✓ (окно перекрытия)"

# ── Шаг 11: Принудительно истекаем старый ключ ────────────────────────────────
say "Шаг 11: Имитация истечения старого ключа"
info "В реальности окно 24ч, но для демо принудительно ставим expires_at в прошлое."
docker compose exec -T db psql -U crawler -d crawlerdb -c \
    "UPDATE api_keys SET expires_at = NOW() - INTERVAL '1 hour' WHERE id = '${KEY_ID}'" > /dev/null 2>&1
info "expires_at сдвинут в прошлое."

# ── Шаг 12: Старый ключ теперь отклоняется ────────────────────────────────────
say "Шаг 12: Старый ключ после истечения"
info "Ожидаем HTTP 401..."
EXPIRE_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/v1/fetch" \
    -H "X-API-Key: ${RAW_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"url":"http://httpbin.org/anything","mode":"static"}')
if [ "$EXPIRE_CODE" = "401" ]; then
    info "HTTP 401 — ключ отклонён. Ротация завершена успешно! ✓"
else
    info "Получен HTTP $EXPIRE_CODE — что-то пошло не так."
fi

# ── Шаг 13: Отзыв ключа ──────────────────────────────────────────────────────
say "Шаг 13: Отзыв (ревокация) ключа"
info "DELETE /v1/keys/{key_id} — требует scope keys"
info "Отзываем старый (уже истёкший) ключ..."
REVOKE=$(curl -sf -X DELETE "$API/v1/keys/${KEY_ID}" \
    -H "X-API-Key: ${OPERATOR_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"reason":"Демо: ключ скомпрометирован"}')
echo "$REVOKE" | python3 -c "
import sys, json
k = json.load(sys.stdin)
print(f'  id={k[\"id\"]}')
print(f'  prefix={k[\"prefix\"]}')
print(f'  revoked_at={k.get(\"revoked_at\",\"N/A\")}')
print(f'  expires_at={k.get(\"expires_at\",\"N/A\")}')
"

# ── Шаг 14: Попытка ротации отозванного ключа → 409 ──────────────────────────
say "Шаг 14: Попытка ротации отозванного ключа"
info "Ожидаем HTTP 409 Conflict..."
ROTATE_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/v1/keys/${KEY_ID}/rotate" \
    -H "X-API-Key: ${OPERATOR_KEY}" \
    -H "Content-Type: application/json")
if [ "$ROTATE_CODE" = "409" ]; then
    info "HTTP 409 Conflict — отозванный ключ нельзя ротировать ✓"
else
    info "Получен HTTP $ROTATE_CODE"
fi

echo ""
echo -e "${BOLD}${GREEN}═══ Демонстрация завершена ═══${NC}"
echo ""
echo "Ключевые моменты:"
echo "  1. Нет самообслуживания — tenant/app/key создаёт оператор"
echo "  2. Ключ возвращается ровно один раз при создании (и при ротации)"
echo "  3. hashed_key никогда не попадает в ответ API"
echo "  4. Ротация: старый ключ работает ещё 24ч, новый — сразу"
echo "  5. Отозванный ключ нельзя ротировать (409)"
echo "  6. После истечения ключ получает 401"
echo "  7. Оператор с admin+keys может работать с любым приложением"
echo "  8. Оператор с keys (без admin) привязан к своему приложению"
