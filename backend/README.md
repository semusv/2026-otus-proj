# Бэкенд GraphRAG Platform (этап 4)

FastAPI-приложение: фундамент (конфиг, логи, корреляция, БД, аутентификация)
+ ingestion-конвейер RusLawOD → чанки → Qdrant + граф Neo4j.
Карточка дополняется на каждом этапе (этап 5 — LangGraph и т.д.).

## Где что находится (`backend/`)

```
backend/
├── pyproject.toml            # зависимости + конфиги ruff/mypy/pytest (uv)
├── uv.lock                   # зафиксированные версии (torch - CPU-индекс PyTorch)
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
│   │   ├── admin.py          # POST /admin/ingest (background task), GET /admin/ingest/status
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
│   ├── llm/
│   │   └── client.py         # LLMClient (OpenAI-совместимый), parse_loose_json
│   ├── ingestion/
│   │   ├── parser.py         # XML RusLawOD → ParsedAct (статусы/даты/keywords/classifier/refs)
│   │   ├── cleaner.py        # чистка <ref>/<span> артефактов разметки
│   │   ├── chunker.py        # разбивка по «Статья N.» (атомарно) + абзацный фолбэк с overlap
│   │   ├── clearance.py      # детерминированный hash(act_id) → PUBLIC/INTERNAL/SECRET
│   │   ├── ontology.py       # маппинг на онтологию графа (ADR-006)
│   │   ├── embeddings.py     # bge-m3 lazy-load CPU (sentence-transformers)
│   │   ├── concepts.py       # LLM-экстракция (:Concept), устойчива к сбоям LLM
│   │   ├── qdrant_writer.py  # коллекция chunks, UUIDv5-идемпотентность, payload-index clearance
│   │   ├── neo4j_writer.py   # MERGE-батчи Act/Authority/Topic/Concept + constraints (+delete_act)
│   │   ├── incremental.py    # план дельты корпуса по sha256 (plan_incremental, FileSnapshot)
│   │   ├── pipeline.py       # оркестратор прогона (run_ingestion): инкрементальный по умолчанию
│   │   └── __main__.py       # CLI: python -m app.ingestion [--dir] [--full] [--concepts|--no-concepts]
│   └── schemas/
│       ├── auth.py           # LoginRequest, TokenResponse, MeResponse
│       ├── admin.py          # IngestStartResponse, IngestStatusResponse
│       └── errors.py         # ErrorResponse (единый формат ошибок)
├── tests/
│   ├── conftest.py           # общая фабрика Settings для тестов
│   ├── fixtures/ruslawod/    # XML-фикстуры по образцу реального корпуса
│   ├── unit/                 # config/middleware/logging/security/parser/chunker/clearance/concepts
│   └── integration/          # auth, admin API, ingestion против compose (PG/Qdrant/Neo4j)
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

### Ingestion (этап 4, инкрементальный режим — задача «Корпус»)

Конвейер: XML RusLawOD → парсер → cleaner → чанкер («Статья N.», атомарно) →
bge-m3 (CPU) → Qdrant (`chunks`, payload: act_id/title/chunk_no/clearance/text) →
граф Neo4j (детерминированные ISSUED_BY/REFERENCES/HAS_TOPIC из метаданных +
LLM-экстракция MENTIONS→Concept, флаг `APP_INGEST_EXTRACT_CONCEPTS`).

Ключевые свойства:
- **Инкрементальность**: каждый файл хешируется sha256 и сравнивается со снапшотом
  прошлого прогона (таблица PG `ingest_files`). Обрабатываются только новые и
  изменившиеся файлы; неизменённые — дешёвый граф-рефреш без эмбеддингов/LLM
  (акты получают REFERENCES на только что добавленные); исчезнувшие из каталога
  файлы — зачистка их актов из Qdrant и Neo4j. Повторный прогон без изменений
  занимает секунды;
- **Идемпотентность**: точки Qdrant — UUIDv5(act_id, chunk_no) + delete перед upsert;
  в Neo4j всё через MERGE; повторный прогон не создаёт дублей;
- **Clearance детерминирован**: md5-хэш от act_id раскладывает акты по
  PUBLIC/INTERNAL/SECRET с процентами из конфига — воспроизводимо между прогонами.
  ВНИМАНИЕ: после смены процентов/чанкера/модели эмбеддингов нужен полный прогон
  (`POST /admin/ingest?full=true` или CLI `--full`), иначе payload/чанки устареют;
- **Сбои не останавливают прогон**: битый XML → в `parse_errors` (в снапшот не
  попадает и ретраится следующим прогоном), недоступный LLM → акт без Concepts.

### Работа с корпусом (как пополнить)

Все способы заканчиваются нажатием «Запустить ingestion» (кнопка в Admin UI или
`POST /admin/ingest`) — прогон подхватит ровно дельту:

1. **Папка на хосте** (compose): докинуть XML в каталог `CORPUS_HOST_DIR`
   (по умолчанию `corpus_test/` репозитория) — маунт живой, без пересборки/перезапуска;
2. **Через API/UI**: `POST /admin/documents` (multipart, admin) — только `.xml`,
   лимит `APP_INGEST_MAX_UPLOAD_MB` на файл, имя без traversal. Загрузка ТОЛЬКО
   сохраняет файлы в каталог корпуса; `DELETE /admin/documents/{filename}` удаляет
   (акты зачистятся при следующем прогоне);
3. **CLI**: `python -m app.ingestion --dir <каталог>` — если корпус в другом месте.

Запуск:

```powershell
# CLI с хоста (env из infra/.env; LM Studio/vLLM нужен только для --concepts)
$env:APP_LLM_BASE_URL = "http://127.0.0.1:1234/v1"   # хост-запуск: не host.docker.internal
$env:APP_LLM_MODEL    = "qwen3.5-2b"                  # НЕ-thinking модель для экстракции
uv --directory backend run python -m app.ingestion --concepts

# или через API (роль admin; корпус смонтирован в контейнер)
POST http://api.localhost/admin/documents       # multipart: загрузить XML (без автозапуска)
POST http://api.localhost/admin/ingest          # 202 старт инкрементального прогона / 409
POST http://api.localhost/admin/ingest?full=true# форс-полный пересчёт
GET  http://api.localhost/admin/ingest/status   # idle|running|done|error + stats (в т.ч. files_skipped/added/changed/removed)
```

> Важно: «думающие» модели (qwen3.5-9b и т.п.) тратят весь бюджет токенов на
> рассуждение и возвращают пустой content — для экстракции Concepts использовать
> не-thinking модель (qwen3.5-2b проверена).

## Конфигурация (все APP_*)

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `APP_ENV` | `dev` | окружение |
| `APP_LOG_LEVEL` | `INFO` | уровень логов |
| `APP_PG_HOST` / `APP_PG_PORT` | `127.0.0.1` / 5432 | PostgreSQL (порт читает также `APP_POSTGRES_PORT`) |
| `APP_PG_USER/PASSWORD/DB` | — обязательны | креды БД |
| `APP_JWT_SECRET` | — обязателен | секрет HS256; без него старт запрещён |
| `APP_JWT_TTL_MINUTES` | `30` | время жизни токена |
| `APP_QDRANT_URL` | — обязательна | REST Qdrant (в compose подменяется на `http://qdrant:6333`) |
| `APP_QDRANT_COLLECTION` | `chunks` | имя коллекции векторов |
| `APP_NEO4J_URI` / `USER` / `PASSWORD` | — / `neo4j` / обязательна | bolt-подключение к графу |
| `APP_LLM_BASE_URL/API_KEY/MODEL` | — / `lm-studio` / обязательна | OpenAI-совместимый endpoint (ADR-001) |
| `APP_EMBEDDING_MODEL/BATCH_SIZE/DEVICE` | `BAAI/bge-m3` / 32 / `cpu` | эмбеддинги (ADR-009) |
| `APP_CHUNK_MAX_CHARS` / `OVERLAP_CHARS` | 1800 / 200 | чанкинг |
| `APP_INGEST_CORPUS_DIR` | `../corpus_test` | каталог XML (в compose: `/data/corpus`) |
| `APP_INGEST_EXTRACT_CONCEPTS` | `false` | LLM-экстракция Concepts |
| `APP_INGEST_INTERNAL_PERCENT` / `SECRET_PERCENT` | 20 / 10 | разметка clearance (остаток PUBLIC) |
| `APP_INGEST_CONCEPT_MAX_PER_CHUNK` | 6 | лимит понятий с чанка |
| `APP_INGEST_MAX_UPLOAD_MB` | 20 | лимит размера файла при `POST /admin/documents` |

Источник значений: env процесса > `infra/.env` (для локальных скриптов) > дефолт.
Шаблон — `infra/.env.example`.

## Тесты

Маркеры pytest: `unit` (быстрые, без зависимостей) · `integration` (нужен PG из
compose) · `gpu_slow` (LLM-as-a-Judge, этап 5+).

| Команда (из корня) | Что гоняет |
|---|---|
| `make lint` | ruff check + mypy — **gate всех коммитов** |
| `make fmt` | ruff format + autofix |
| `make test-unit` | 122 теста: конфиг, корреляция, логи, JWT, парсер/чанкер/clearance/онтология, LLM-моки, инкрементальный план корпуса |
| `make test-integration` | против compose: auth-флоу в изолируемой PG, admin API, ingestion (Qdrant+Neo4j, идемпотентность), инкрементальные прогоны (add/change/remove), upload/delete документов |
| `make test-all` | unit + integration |
| `make seed-users` | (пере)создать viewer/analyst/admin в основной БД `graphrag` |
| `make openapi-export` | перегенерировать `docs/api/openapi.yaml` после правок эндпоинтов |

Интеграционные тесты сами создают и удаляют свою БД — основную не трогают;
ingestion-тесты используют отдельную коллекцию `chunks_test` и чистят граф после себя.
Нужен запущенный стек: `docker compose -f infra/docker-compose.yml up -d postgres qdrant neo4j`.
Postman/Newman E2E-коллекция — `tests/postman/graphrag.postman_collection.json`
(gate этапа 9: `make test-postman`).

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

- этап 5: `app/agents/graph.py` (LangGraph state machine), memory/planner/tools, SSE `/api/chat`;
- этап 6: ACL pre-fetch фильтры (Qdrant payload filter + WHERE в Cypher);
- этап 7: OTel-трейсы спанов узлов графа, Prometheus-метрики, audit_log из guardrails.
