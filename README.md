# Crawler API

Сервис получения веб-страниц через HTTP и браузеры с очередью задач, управлением прокси и WARC-архивом в локальном S3.

Стек: FastAPI → Redis/arq → worker; PostgreSQL хранит настройки, ключи, аудит и индекс архивов; RustFS — объекты WARC.

## Возможности

- Четыре режима: `static` — HTTPX, `stealth` — curl_cffi, `browser` — Playwright/Chromium, `camoufox` — Camoufox/Firefox.
- Одиночные и пакетные задачи, опрос результатов, ожидание одиночной задачи до 30 секунд.
- Доменные политики, escalation tiers, прокси-пулы, health/cooldown и circuit breaker; импорт и фоновая проверка кандидатов.
- Tenants, приложения, API-ключи со scopes, ротация и статистика использования.
- WARC/S3: дисковый журнал перед отправкой, повторная доставка и восстановление индекса после ошибок S3/БД.
- SSRF-проверки, rate limiting, readiness и Prometheus-метрики. Успешный доступ к любому сайту не гарантируется.

## Деплой

Нужны Linux x86_64, Git, Docker с Compose v2, интернет для первой сборки и место под браузеры и архивы. Нативный ARM64 текущим образом не поддерживается. Обязательного `.env` нет.

Свежая установка из `master`:

```bash
git clone --branch master https://github.com/softK1T/crawler-api.git
cd crawler-api
docker compose -p crawler-current -f docker-compose.yml build api
docker compose -p crawler-current -f docker-compose.yml pull postgres redis s3
docker compose -p crawler-current -f docker-compose.yml up -d --no-build --pull never --wait --wait-timeout 300
curl --fail-with-body http://127.0.0.1:8000/readyz
```

Выполняй следующий шаг только после успешного предыдущего. Общий образ собирает только `api`. При старте создаются постоянные секреты, выполняются миграции и подготовка администратора. Завершение `secrets` и `initialize` с кодом 0 нормально; API, worker, PostgreSQL, Redis и S3 должны работать.

| Компонент | Адрес / хранение |
|---|---|
| API | `http://127.0.0.1:8000`; порт хоста меняется через `API_PORT` |
| Swagger / ReDoc | `/docs` / `/redoc`; схемы запросов и ответов — `/openapi.json` |
| S3 | `http://s3:9000` внутри Docker; bucket `crawler-warc`, регион `us-east-1` |
| Консоль S3 | `http://127.0.0.1:9001`; порт хоста — `S3_CONSOLE_PORT` |
| Постоянные тома | `postgres-data`, `redis-data`, `runtime-secrets`, `s3-data`, `s3-secrets`, `warc-spool` с префиксом Compose-проекта |

Обновление существующего канонического стека — из каталога проекта, с тем же именем Compose-проекта:

```bash
git pull --ff-only origin master
docker compose -p crawler-current -f docker-compose.yml build api
docker compose -p crawler-current -f docker-compose.yml pull s3
docker compose -p crawler-current -f docker-compose.yml up -d --no-build --pull never --wait --wait-timeout 300
```

Не меняй имя проекта при обновлении: это может подключить другие тома. Не объединяй `docker-compose.yml` с устаревшим самостоятельным `compose.ubuntu.yaml`. Миграция внешней установки требует отдельного плана.

Если запуск упал, сначала смотри состояние и лог конкретного сервиса, а не удаляй данные:

```bash
docker compose -p crawler-current -f docker-compose.yml ps -a
docker compose -p crawler-current -f docker-compose.yml logs --no-color --tail=120 secrets
```

## Использование

Все защищённые методы принимают заголовок `X-API-Key`. Получить существующий bootstrap-ключ в переменную, не выводя его:

```bash
API_KEY=$(docker compose -p crawler-current -f docker-compose.yml exec -T api python scripts/stack_runtime.py key)
curl --fail-with-body http://127.0.0.1:8000/v1/keys -H "X-API-Key: $API_KEY"
```

Не включай `set -x`, не коммить ключи и не присылай их вместе с логами. Для клиентов выпускай отдельные ключи с минимальными scopes. Новый `raw_key` при выпуске/ротации возвращается один раз. `admin` не заменяет автоматически остальные scopes.

Пример запроса без прокси:

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/fetch   -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json'   -d '{"url":"https://example.org/","mode":"static","use_proxy":false,"options":{"sync":true}}'
```

Без `options.sync` метод возвращает `202` и `job_id`. С `sync=true` ожидает до 30 секунд: завершённая задача возвращается с HTTP 200, иначе — 202; продолжай опрашивать `GET /v1/jobs/{job_id}`. HTTP 200 не заменяет проверку поля `status`: задача может быть `failed`. Идемпотентность задаётся полем JSON `idempotency_key`, не заголовком.

| Настройка канонического Compose | Значение по умолчанию |
|---|---|
| WARC / локальный S3 | Включены |
| Проверка кандидатов прокси | Включена; кандидатов нужно импортировать |
| Webshare sync / premium proxy tiers | Выключены; включаются явно через `PROXY_SYNC_ENABLED` / `ENABLE_PREMIUM_PROXY_TIERS`; sync требует `WEBSHARE_API_KEY` |
| Worker / Chromium pool | `WORKER_MAX_JOBS=2` / 1 браузер |
| Callbacks / tracing | Выключены; для включения нужны изменения окружения контейнеров и соответствующие секреты/сервисы |

Произвольные переменные из `.env` не попадают в контейнеры автоматически: Compose должен явно их передавать. Геолокация кандидата не подтверждает тип прокси или доступ к целевому сайту.

## Эндпоинты

Пути указаны полностью, включая завершающий `/`. «Ключ» означает любой действующий API-ключ; дополнительные ограничения области данных сохраняются. `fetch`, `archive`, `keys`, `admin` — требуемые scopes. Схемы тел и все параметры смотри в `/docs` или `/openapi.json` текущего экземпляра.

### Получение страниц, пакеты и архив

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| POST | `/v1/fetch` | `fetch` | Создать одиночную задачу |
| GET | `/v1/jobs/{job_id}` | Ключ, своё приложение | Статус, результат или ошибка задачи |
| POST | `/batches/` | `fetch` | Создать пакет задач |
| GET | `/batches/{batch_id}/status` | `fetch`, своё приложение | Состояние пакета |
| GET | `/batches/{batch_id}/results` | `fetch`, своё приложение | Результаты пакета |
| GET | `/v1/archive/` | `archive` | Список записей: `url`, `from`, `to`, `page`, `per_page` |
| GET | `/v1/archive/{entry_id}` | `archive` | Содержимое записи, тело в `body_b64`; ID записи, не задачи |

Архивные маршруты регистрируются только при `ENABLE_WARC=true`. Не считай scope `archive` гарантией изоляции архивов между приложениями: текущие запросы архива не фильтруют записи по приложению; выдавай его только доверенным клиентам.

### Tenants, приложения, проекты и ключи

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| POST | `/v1/tenants` | `admin` | Создать tenant |
| GET | `/v1/tenants` | `admin` | Список tenants |
| GET | `/v1/tenants/{tenant_id}` | `admin` | Получить tenant |
| POST | `/projects/` | `admin` | Альтернативный маршрут создания tenant, не отдельная сущность |
| GET | `/projects/` | `admin` | Список tenants без обёртки пагинации `/v1/tenants` |
| POST | `/v1/applications` | `admin` | Создать приложение внутри tenant |
| GET | `/v1/applications` | Ключ | Свои приложения; администратору — все |
| GET | `/v1/applications/{app_id}` | Ключ | Своё приложение; администратору — любое |
| PATCH | `/v1/applications/{app_id}` | `admin` | Изменить имя, owner label или активность |
| POST | `/v1/keys` | `keys` | Выпустить ключ с проверкой выдаваемых scopes и приложения |
| GET | `/v1/keys` | Ключ | Метаданные ключей своего приложения |
| DELETE | `/v1/keys/{key_id}` | `keys` | Отозвать ключ; не текущий ключ аутентификации |
| POST | `/v1/keys/{key_id}/rotate` | `keys` | Выпустить преемника с периодом перекрытия |

Для операций над ключами другого приложения дополнительно нужен `admin`. Выдача scope `keys` тоже требует `admin`; выдавать scopes шире собственных нельзя.

### Доменные политики и создание прокси-пулов

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| POST | `/admin/domain-policies` | `admin` | Создать или обновить политику домена |
| GET | `/admin/domain-policies` | `admin` | Список политик |
| GET | `/admin/domain-policies/{policy_id}` | `admin` | Получить политику |
| PATCH | `/admin/domain-policies/{policy_id}` | `admin` | Изменить политику |
| DELETE | `/admin/domain-policies/{policy_id}` | `admin` | Удалить политику |
| POST | `/admin/domain-policies/{policy_id}/pin-tier` | `admin` | Зафиксировать tier 0–6 или снять блокировку; `tier` и `locked` — query-параметры |
| POST | `/admin/proxy-pools` | `admin` | Создать прокси-пул |
| POST | `/admin/proxy-pools/{pool_id}/proxies` | `admin` | Добавить прокси в пул |

### Прокси и проверка кандидатов

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/proxy/pools` | Ключ | Активные пулы |
| GET | `/proxy/pools/{pool_id}/stats` | Ключ | Health-статистика пула |
| GET | `/proxy/proxies` | `admin` | Список прокси, фильтры `pool_id`, `country` |
| GET | `/proxy/proxies/{proxy_id}/events` | `admin` | События прокси |
| POST | `/proxy/health` | `admin` | Записать результат использования прокси |
| POST | `/proxy/reset/{proxy_id}` | `admin` | Сбросить health и cooldown |
| DELETE | `/proxy/circuit-breaker/{domain}` | `admin` | Сбросить circuit breaker домена |
| POST | `/proxy/admin/proxies` | `admin` | Массово импортировать структурированные записи прокси |
| POST | `/proxy/candidates/import` | `admin` | Импортировать до 500 URL в активный пул `public-candidates` |
| PATCH | `/proxy/candidates/{proxy_id}/enabled` | `admin` | Разрешить проверку/автоактивацию или поставить на паузу |
| POST | `/proxy/candidates/pools/{pool_id}/recheck` | `admin` | Поставить разрешённых кандидатов на повторную проверку |
| GET | `/proxy/candidates/pools/{pool_id}/stats` | `admin` | Сводка по странам и статусам |
| GET | `/proxy/candidates/pools/{pool_id}/results` | `admin` | Результаты проверки с `limit` и `offset` |

### Использование и служебные маршруты

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/v1/usage/` | Ключ | Статистика своего приложения, `from_month` / `to_month` |
| GET | `/v1/usage/applications/{application_id}` | `admin` | Статистика выбранного приложения |
| GET | `/healthz` | Без ключа | Процесс API отвечает; не проверка зависимостей |
| GET | `/readyz` | Без ключа | Готовность БД, Redis, worker, браузеров и включённого S3 |
| GET | `/metrics` | Без ключа | Prometheus; путь настраивается через `METRICS_PATH` |
| GET | `/docs` | Без ключа | Swagger UI |
| GET | `/redoc` | Без ключа | ReDoc |
| GET | `/openapi.json` | Без ключа | OpenAPI-схема |
| GET | `/docs/oauth2-redirect` | Без ключа | Вспомогательный redirect Swagger, не API входа |

## Хранение и безопасность

- Redis хранит временные результаты задач; постоянный архив — WARC в S3 плюс индекс PostgreSQL. Новый WARC содержит один полный ответ; дедупликация через revisit не используется.
- При отказе S3/БД задача получает ошибку, а записанный дисковый журнал остаётся для повторной доставки. Восстановление архива не меняет статус уже упавшей задачи. Журнал ограничен 5 GiB; при заполнении новые записи отклоняются, старые не вытесняются.
- Не запускай `down -v`, `prune` и тесты против рабочих БД/Redis. Тома не защищают от потери диска: нужен согласованный backup БД, S3, секретов и журнала вне сервера.
- API и S3-консоль доступны только на loopback, S3 API — внутри Docker. Для внешнего доступа нужны TLS/reverse proxy, ограничение доступа к служебным маршрутам и настройка CORS. Не отключай SSRF для недоверенных URL.
- S3 сейчас выделен этому проекту и использует общую административную пару для приложения и сервера хранения; не раздавай её другим приложениям. Loopback-конфигурация не является завершённым аудитом безопасности production/multi-tenant.

Подробности архивирования и восстановления: [LOCAL-S3](docs/LOCAL-S3.md). Остальные материалы: [документация](docs/README.md).
