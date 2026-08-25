# Бэкенд GraphRAG Platform (этап 3)

FastAPI-приложение: фундамент — конфиг, логи, корреляция запросов, БД, аутентификация.
Карточка дополняется на каждом этапе (этап 4 — ingestion, этап 5 — LangGraph и т.д.).

## Где что находится (`backend/`)

```
backend/
├── pyproject.toml            # зависимости + конфиги ruff/mypy/pytest (uv)
├── uv.lock                   # зафиксированные версии
├── Dockerfile                # multi-stage: deps через uv → рантайм python:3.12-slim
├── alembic.ini               # миграции; URL берётся из APP_*-переменных
├── migrations/versions/      # 0001 — users/roles/sessions/audit_log (+сид ролей)
├── app/
│   ├── main.py               # create_app(): фабрика; uvicorn --factory app.main:create_app
│   ├── config.py             # Settings (pydantic-settings): все APP_*, fail-fast на старте
│   ├── api/
│   │   ├── __init__.py       # api_router — агрегатор роутеров
│   │   ├── system.py         # GET /health, GET /metrics (заглушка до этапа 7)
│   │   ├── auth.py           # POST /auth/login, GET /auth/me
│   │   └── deps.py           # get_current_user (Bearer → JWT → сессия в PG), audit_context()
│   ├── core/
│   │   ├── security.py       # bcrypt, JWT HS256 (sub/role/jti/exp), resolve_clearances(role)
│   │   ├── errors.py         # доменные ошибки → {code, message}; хендлеры FastAPI
│   │   ├── logging.py        # JSON-логи + ContextFilter (инъекция trace/request_id)
│   │   └── context.py        # contextvar'ы request_id / trace_id
│   ├── middleware/
│   │   └── correlation.py    # чистый ASGI: эхо X-Trace-Id, генерация X-Request-Id
│   ├── db/
│   │   ├── base.py           # Base(DeclarativeBase), Database (engine+сессии), get_session
│   │   ├── models.py         # User, Role(RoleName), AuthSession, AuditLog
│   │   └── seed.py           # сидинг ролей и 3 пользователей
│   └── schemas/
│       ├── auth.py           # LoginRequest, TokenResponse, MeResponse
│       └── errors.py         # ErrorResponse (единый формат ошибок)
├── tests/
│   ├── conftest.py           # общая фабрика Settings для тестов
│   ├── unit/                 # config / middleware / logging / security
│   └── integration/          # auth-флоу против реальной PG (compose)
└── .venv/                    # создаётся `uv sync` (в git не входит)
```

Корневые скрипты-обёртки: `scripts/seed_users.py`, `scripts/export_openapi.py`.

## Как это работает

### Запуск (docker-compose)

`backend` из стека стартует командой
`alembic upgrade head && uvicorn app.main:create_app --factory ...`:
миграции применяются до поднятия HTTP. Без обязательных `APP_*` процесс падает
на старте с понятной ошибкой валидации (fail-fast). Роутинг: браузер → traefik
(`api.localhost`) → backend.

### Поток запроса

```
запрос → CorrelationIdMiddleware (X-Trace-Id эхо / X-Request-Id, contextvars,
         access-лог JSON) → роутер → сервис/БД → ответ + заголовки корреляции
```

Каждая лог-строка — один JSON с `trace_id`/`request_id`; разбор инцидента:
`X-Trace-Id из ответа → трейс (Jaeger, этап 7) → логи с этим trace_id`.

### Аутентификация (ADR-007)

1. `POST /auth/login`: bcrypt-проверка пароля → строка в `sessions` (PK = будущий `jti`)
   → JWT HS256 `{sub, role, jti, exp}` (TTL `APP_JWT_TTL_MINUTES`);
2. `GET /auth/me` (и всё защищённое далее): Bearer → decode → проверка сессии в PG
   (не отозвана/не истекла) → активный пользователь;
3. `resolve_clearances(role)` — единственная точка «роль → метки доступа»
   (`viewer→PUBLIC`, `analyst→+INTERNAL`, `admin→+SECRET`); на этапе 6 эти метки
   идут в pre-fetch ACL-фильтры Qdrant/Neo4j.

Логины (успех/неудачa) пишутся в `audit_log` c `request_id`/`trace_id`.
Демо-пользователи: `viewer/analyst/admin` (пароли по умолчанию — только dev).

## Конфигурация (все APP_*)

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `APP_ENV` | `dev` | окружение |
| `APP_LOG_LEVEL` | `INFO` | уровень логов |
| `APP_PG_HOST` / `APP_PG_PORT` | `127.0.0.1` / 5432 | PostgreSQL (порт читает также `APP_POSTGRES_PORT`) |
| `APP_PG_USER/PASSWORD/DB` | — обязательны | креды БД |
| `APP_JWT_SECRET` | — обязателен | секрет HS256; без него старт запрещён |
| `APP_JWT_TTL_MINUTES` | `30` | время жизни токена |

Источник значений: env процесса > `infra/.env` (для локальных скриптов) > дефолт.
Шаблон — `infra/.env.example`.

## Тесты

Маркеры pytest: `unit` (быстрые, без зависимостей) · `integration` (нужен PG из
compose) · `gpu_slow` (LLM-as-a-Judge, этап 5+).

| Команда (из корня) | Что гоняет |
|---|---|
| `make lint` | ruff check + mypy — **gate всех коммитов** |
| `make fmt` | ruff format + autofix |
| `make test-unit` | 25 тестов: fail-fast конфига, заголовки корреляции, JSON-логи, bcrypt/JWT (roundtrip, просрочка, кривая подпись), матрица clearances |
| `make test-integration` | 8 тестов против PG: поднимает **изолируемую** БД `graphrag_itg_<hex>`, применяет миграции, прогоняет login/me/ошибки, проверяет строки sessions/audit_log |
| `make test-all` | unit + integration |
| `make seed-users` | (пере)создать viewer/analyst/admin в основной БД `graphrag` |
| `make openapi-export` | перегенерировать `docs/api/openapi.yaml` после правок эндпоинтов |

Интеграционные тесты сами создают и удаляют свою БД — основную не трогают;
нужен только запущенный контейнер postgres (`docker compose -f infra/docker-compose.yml up -d postgres`).
Postman/Newman-коллекция auth — `tests/postman/collection.json` (см. его README).

## Повседневные команды

```powershell
# локально: дев-окружение и гейты
uv --directory backend sync     # после git pull (обновился lock)
make lint && make test-unit

# запуск бэкенда в контейнере с правками кода
docker compose -f infra/docker-compose.yml up -d --build backend

# логи приложения (JSON)
docker compose -f infra/docker-compose.yml logs -f backend

# проверить руками через Traefik
curl.exe --noproxy "*" -X POST http://api.localhost/auth/login ^
  -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"admin123\"}"
```

## Планы карточки (дозаполняется)

- этап 4: модуль `app/ingestion/` — парсер XML RusLawOD, чанкер, bge-m3, Qdrant/Neo4j writers, `POST /admin/ingest`;
- этап 5: `app/agents/graph.py` (LangGraph state machine), memory/planner/tools, SSE `/api/chat`;
- этап 6: ACL pre-fetch фильтры (Qdrant payload filter + WHERE в Cypher);
- этап 7: OTel-трейсы спанов узлов графа, Prometheus-метрики, audit_log из guardrails.
