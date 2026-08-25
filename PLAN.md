# MVP Roadmap: GraphRAG-платформа корпоративных знаний

> План рассчитан на независимые сессии: открыть файл → взять первый незакрытый этап
> (`[ ]`) → выполнить Deliverables → пройти Acceptance → отметить чекбокс и закоммитить.

## Контекст (читать в начале каждой сессии)

- **Цель:** E2E конвейер GraphRAG в закрытом контуре по `tasks.md` (on-premise, без внешних API).
- **Тема реализации:** Advanced RAG → **GraphRAG (Knowledge Graphs)**. Простой векторный поиск не принимается.
- **Датасет:** RusLawOD v3 (XML правовых актов РФ, 1991–2025): https://github.com/irlcode/RusLawOD. Тестовый корпус — `corpus_test/` (100 файлов).
- **Референс структуры backend:** https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template (пишем с нуля, оглядываясь на структуру).
- **Стек:** Python 3.11+ / FastAPI · LangGraph · LLM Serving: dev — LM Studio (OpenAI-compatible, `APP_LLM_BASE_URL`), целевой — vLLM Qwen3-8B-AWQ (ADR-001, дополнение) · Qdrant · Neo4j Community · PostgreSQL 16 · Traefik · OTel→Jaeger/Prometheus/Grafana · Langfuse (внешний docker-инстанс) · React SPA.
- **Железо:** RTX 5070 Ti 16GB VRAM (Blackwell/sm_120 → нужен образ vLLM с CUDA ≥12.8), Windows + Docker Desktop (WSL2, GPU passthrough).
- **Онтология графа (4 типа узлов, не усложнять):**
  - `(:Act {id, title, doc_number, date, status})`
  - `(:Authority {name})`
  - `(:Topic {name})`
  - `(:Concept {name})` — юридические понятия, извлекаемые LLM из текста чанков
  - Рёбра: `(:Act)-[:ISSUED_BY]->(:Authority)`, `(:Act)-[:REFERENCES]->(:Act)`, `(:Act)-[:HAS_TOPIC]->(:Topic)`, `(:Act)-[:MENTIONS]->(:Concept)`
  - Гибридная экстракция: ISSUED_BY / REFERENCES / HAS_TOPIC — детерминированно из метаданных XML; MENTIONS — LLM-экстракцией (демонстрация нейро-символического подхода GraphRAG)
- **RBAC / Security-by-Design:** метки доступа `PUBLIC / INTERNAL / SECRET` на чанках (payload Qdrant) и узлах графа; фильтрация ДО retrieval (pre-fetch). Часть актов датасета помечается SECRET для демонстрации.
- **Auth:** отдельного сервиса авторизации НЕТ. JWT выпускает сам backend (`POST /auth/login`), проверка подписи HS256, секрет из `.env`. Пользователи/роли/сессии/аудит — в PostgreSQL. Keycloak — rejected alternative (см. ADR-007).
  - Роли: `viewer → [PUBLIC]`, `analyst → [PUBLIC, INTERNAL]`, `admin → [все]`.
- **API-контракт (Swagger/OpenAPI):** контракт — первоклассный артефакт для дружбы бэкенда и фронта.
  - FastAPI генерирует OpenAPI-схему автоматически; скрипт экспорта сохраняет её в `docs/api/openapi.yaml`, файл коммитится при каждом изменении эндпоинтов;
  - Swagger UI доступен через Traefik на `/docs` (dev);
  - фронтенд получает типы из контракта генерацией (`openapi-typescript`) — единый источник правды, без ручного дублирования моделей;
  - стиль контракта — по образцу `Old labs/.../docs/api/openapi.yaml`: `X-Trace-Id`, идемпотентность, описанные ошибки, примеры запросов.
- **Observability — три столпа заложены с самого начала (базовый уровень в каждом этапе бэкенда):**
  - **Трейсы:** OpenTelemetry (W3C `traceparent`), сквозная корреляция от фронта: клиент генерирует/передаёт `X-Trace-Id` (+ `traceparent`), Traefik пробрасывает заголовки, backend продолжает контекст, все исходящие вызовы (vLLM, Qdrant, Neo4j, Langfuse) идут внутри одного трейса;
  - **Метрики:** Prometheus `/metrics` на backend (RPS, latency-гистограммы по эндпоинтам и узлам графа, ошибки) + нативные метрики vLLM (tokens/sec);
  - **Логи:** структурные JSON-логи, request-id middleware, инъекция `trace_id`/`request_id` в каждую запись через logging-filter;
  - Правило отладки: любой разбор инцидента идёт по цепочке «X-Trace-Id из ответа API → трейс в Jaeger → логи с этим trace_id».
- **Развёртывание:** разработка и проверка — docker-compose; minikube/Helm — последний этап.
- **Правила этапов:** этап считается закрытым только при зелёных тестах.
- **Git-дисциплина:** коммит после каждого успешного шага/этапа (зелёные тесты = триггер коммита) —
  чтобы в любой момент можно было откатиться к последней рабочей точке:
  - формат сообщений: `stage-N(scope): что сделано` (напр. `stage-4(ingestion): XML parser + chunker`);
  - по завершении этапа — тег `stage/N` (напр. `git tag stage/4`) как точка отката;
  - мелкие подшаги внутри этапа тоже коммитятся (парсер → чанкер → writer), не копим большие диффы;
  - откат: `git reset --hard <tag|sha>` или `git revert` для общих веток.
- **Качество кода / конфигурация (обязательное правило всех этапов):**
  - Все настройки ТОЛЬКО через переменные окружения: pydantic-settings, префикс `APP_`, типизированная модель конфига с fail-fast валидацией на старте.
  - `.env.example` — полный, актуальный, коммитится; реальные секреты не коммитятся никогда.
  - Ноль хардкодов в коде: URL сервисов, имена моделей, размеры коллекций, порты — только из конфига.
  - Линтеры с первого этапа бэкенда: ruff + mypy + pre-commit; `make lint` — обязательный gate.
- **Ограничения РФ (план Б, не усложняем заранее):** по умолчанию используем официальные источники
  (Docker Hub, PyPI, HuggingFace) — доступ скорее всего есть. Зеркала подключаем ТОЛЬКО если что-то
  фактически недоступно: переключение делаем одной переменной в `.env` (`APP_DOCKER_REGISTRY`,
  `HF_ENDPOINT`, `PIP_INDEX_URL`) без правок кода. Детали и список проверенных зеркал — ADR-011.

## Структура репозитория (целевая)

```
/
├── PLAN.md                  # этот файл
├── tasks.md                 # исходное задание
├── docs/
│   ├── index.md             # ADD: обзор архитектуры, глоссарий
│   ├── adr/                 # ADR-001..011
│   ├── architecture/        # *.drawio + dataflow.md
│   ├── api/
│   │   └── openapi.yaml     # версионированный контракт API (экспорт из FastAPI)
│   └── load-report.md       # этап 9
├── infra/
│   ├── docker-compose.yml           # ядро стека
│   ├── docker-compose.gpu.yml       # override с vLLM на GPU
│   ├── docker-compose.observability.yml
│   ├── traefik/
│   ├── grafana/
│   └── helm/                        # этап 10 (minikube)
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py                  # FastAPI entrypoint
│   │   ├── config.py                # pydantic-settings
│   │   ├── api/                     # роутеры: auth, chat(SSE), admin/ingest, health
│   │   ├── core/security.py         # JWT, роли → ACL-метки
│   │   ├── agents/
│   │   │   ├── graph.py             # LangGraph state machine (+ Agent Loop)
│   │   │   ├── memory.py            # Memory module: история сессии из PG
│   │   │   └── planner.py           # Planner: выбор инструментов/итераций
│   │   ├── rag/
│   │   │   ├── tools.py             # Tools Interface: инструменты агента
│   │   │   └── ...                  # vector_retriever, graph_retriever, reranker, fusion
│   │   ├── llm/client.py            # async клиент vLLM (OpenAI SDK) + фабрика промптов
│   │   ├── ingestion/               # XML→чанки→эмбеддинги→экстракция→запись в БД
│   │   ├── db/                      # SQLAlchemy async модели, репозитории
│   │   └── observability/           # OTel setup, метрики, структурные логи
│   └── tests/                       # pytest: unit / integration / gpu_slow
├── frontend/                # Vite + React + TS (минимальный)
├── tests/postman/           # коллекция + окружение для Newman
└── scripts/                 # download_dataset.sh, seed_users.py, load_test, run_postman
```

## Этапы

### [x] Этап 1. Документация и ADR
**Deliverables:**
- `docs/index.md` — ADD-обзор, глоссарий, навигация
- `docs/adr/ADR-001..010.md` (рус., формат: Контекст → Решение → Trade-offs (таблица) → Последствия):
  1. LLM Serving (vLLM vs SGLang vs TGI; нюанс Blackwell/CUDA 12.8)
  2. Модель (Qwen3-8B-AWQ vs T-lite vs Saiga vs Qwen3-14B-AWQ; бюджет 16GB VRAM: веса + KV-cache)
  3. Vector DB (Qdrant vs Milvus vs Weaviate)
  4. Graph DB (Neo4j Community vs NebulaGraph)
  5. Оркестрация (LangGraph vs LlamaIndex Workflows)
  6. GraphRAG-подход (онтология 4 узлов; гибридная экстракция: детерминированные рёбра из метаданных XML + LLM-экстракция Concept)
  7. Security/RBAC (ACL pre-fetch, JWT; Keycloak — rejected alternative)
  8. Observability (OTel→Jaeger/Prometheus/Grafana; Langfuse внешний)
  9. Embeddings (bge-m3 + bge-reranker-v2-m3; CPU-in-process trade-off)
  10. Deployment strategy (compose dev → minikube target)
  11. Устойчивость к ограничениям доступа из РФ: план Б (зеркала Docker Hub/PyPI/HF) — применяется только при фактической недоступности, дефолт — официальные источники
- `docs/architecture/*.drawio`: C4 L1 Context, C4 L2 Container, C4 L3 Agent Component, Deployment, Sequence(query flow), ER
  Обязательное содержание по заданию:
  - **L1:** внешние системы ERP, CRM, User Channels
  - **L2:** API Gateway, Vector DB, LLM Serving Engine, Orchestrator, Frontend + группировка Control Plane (traefik/backend/frontend) vs Data Plane (vLLM/Qdrant/Neo4j/PG)
  - **L3:** явные компоненты агента: Memory module, Planner, Tools Interface (+ guardrails, retrievers)
  - **Deployment:** сегментация DMZ / Internal / GPU-зона, Vault, балансировка нагрузки (реплики backend за Traefik)
  - **Sequence:** User → Guardrails → Rerank → Agent Loop → Tool Execution → Response (с циклом re-plan)
  - **ER:** чанки, векторы (payload Qdrant), история сессий, логи запросов, RBAC-таблицы PG, узлы графа
- `docs/architecture/dataflow.md` — Data Flow диаграмма/описание

Чек-лист выполнения:

**A. Каркас документации**
- [x] Структура `docs/`: `index.md`, `adr/`, `architecture/`, `api/` (пока пустая)
- [x] `docs/index.md` — ADD-обзор: цель системы, ключевые решения сводной таблицей, глоссарий, навигация на все документы

**B. ADR-001..011 (рус., формат: Контекст → Решение → Trade-offs таблицей → Последствия)**
- [x] ADR-001 LLM Serving: vLLM vs SGLang vs TGI (+ нюанс Blackwell sm_120 → CUDA ≥12.8)
- [x] ADR-002 Модель: Qwen3-8B-AWQ vs T-lite/T-pro vs Saiga vs Qwen3-14B-AWQ (бюджет 16GB: веса + KV-cache)
- [x] ADR-003 Vector DB: Qdrant vs Milvus vs Weaviate (+ фильтрация payload для ACL)
- [x] ADR-004 Graph DB: Neo4j Community vs NebulaGraph
- [x] ADR-005 Оркестрация: LangGraph vs LlamaIndex Workflows
- [x] ADR-006 GraphRAG: онтология 4 узлов, гибридная экстракция (метаданные XML без LLM + LLM для Concept)
- [x] ADR-007 Security/RBAC: JWT от backend, pre-fetch ACL; Keycloak — rejected alternative
- [x] ADR-008 Observability: OTel→Jaeger/Prometheus/Grafana; Langfuse внешний
- [x] ADR-009 Embeddings: bge-m3 + bge-reranker-v2-m3; CPU-in-process trade-off
- [x] ADR-010 Deployment: compose dev → minikube target
- [x] ADR-011 РФ-доступность: дефолт — официальные источники, план Б — зеркала через env-переменные

**C. Диаграммы draw.io (`docs/architecture/`)**
- [x] `c4-l1-context.drawio` — пользователи/роли, наша система чёрным ящиком, ERP, CRM, User Channels
- [x] `c4-l2-container.drawio` — Frontend, Traefik (API Gateway), Backend/Orchestrator, vLLM, Qdrant, Neo4j, PG, Vault, Jaeger/Prometheus/Grafana/Langfuse; группы Control Plane vs Data Plane
- [x] `c4-l3-agent-component.drawio` — Guardrails in/out, Planner, Memory module, Tools Interface, retrievers, fusion, reranker, generator, evaluator (+ цикл re-plan)
- [x] `deployment.drawio` — зоны DMZ / Internal / GPU-зона, балансировка (реплики backend за Traefik), Vault
- [x] `sequence-query.drawio` — User → Guardrails → Rerank → Agent Loop (re-plan ≤2) → Tool Execution → Response + проброс `X-Trace-Id`
- [x] `er-model.drawio` — RBAC-таблицы PG, чанки+vectors (payload Qdrant), узлы графа Neo4j
- [x] `dataflow.md` — потоки ingestion и query

**D. Приёмка этапа**
- [x] Сверка покрытия с `tasks.md`: C4 L1–L3 ✓ Deployment ✓ Data Flow ✓ Sequence ✓ ER ✓ trade-offs ✓ Security-by-Design ✓
- [x] Каждая `.drawio` проверена открытием в draw.io (валидация парсинга через draw.io MCP)
- [x] Все ссылки из `index.md` рабочие
- [x] Коммит `stage-1(docs): ...` + тег `stage/1`

**Acceptance:** покрыты все критерии tasks.md (Deployment ✓ Data Flow ✓ trade-offs ✓ Security-by-Design ✓); диаграммы открываются в draw.io.

### [x] Этап 2. Инфраструктура docker-compose
**Deliverables:** `infra/docker-compose.yml` (+override gpu, +observability), конфиги Traefik/Grafana provisioning, `.env.example`, smoke-скрипт.
Сервисы: traefik, backend (заглушка `/health`), vllm (GPU), qdrant, neo4j, postgres, vault (dev-mode — хранение секретов по заданию), otel-collector, jaeger, prometheus, grafana. Langfuse подключается внешне по URL из `.env`. Образы — с официального Docker Hub (при блокировке переключаемся на зеркало одной переменной, см. ADR-011); версии фиксируются.
**Acceptance:** `docker compose up -d` → все healthchecks green; UI Jaeger/Grafana/Neo4j Browser доступны через Traefik.
**Тесты:** `scripts/smoke_infra.py` — проверка портов/health всех сервисов.

> Примечание (по итогам выполнения): vLLM выведен из обязательного набора этапа 2 —
> `docker-compose.gpu.yml` остаётся опциональным override'ом (целевой движок демо/нагрузки).
> LLM-эндпоинт конфигурируется переменными `APP_LLM_BASE_URL/APP_LLM_MODEL`; в dev-режиме
> используется LM Studio на хосте. См. ADR-001, раздел «Дополнение».

### [x] Этап 3. Фундамент бэкендa
**Deliverables:** app factory, pydantic-settings (fail-fast валидация `APP_*` конфига на старте), SQLAlchemy-модели (users, roles, sessions, audit_log), миграции (alembic или create_all для MVP), `POST /auth/login` (JWT), `GET /auth/me`, middleware: request-id/trace-correlation (`X-Trace-Id` входящий/эхо в ответ, `X-Request-Id`) + структурное JSON-логирование с инъекцией `trace_id`/`request_id` в каждую строку. Сид-скрипт 3 пользователей (viewer/analyst/admin), структура модулей из схемы выше. Настроить ruff + mypy + pre-commit + Makefile (`lint`, `test-*`) — далее это gate всех этапов.
**OpenAPI:** настроить метаданные FastAPI (title/version/description, теги), скрипт `scripts/export_openapi.py` → `docs/api/openapi.yaml` (коммитится), Swagger UI проверен через Traefik. Postman-коллекция этапа создаётся импортом из openapi.yaml.
**Acceptance:** логин выдаёт валидный JWT, `/auth/me` возвращает роль; `make lint` и pytest зелёные; запуск без обязательных env падает с понятной ошибкой валидации; `docs/api/openapi.yaml` содержит auth-эндпоинты со схемами ошибок.
**Тесты:** pytest unit (jwt sign/verify, резолв ACL), integration против PG из compose. Postman: auth-эндпоинты.

### [ ] Этап 4. Ingestion pipeline
**Deliverables:** парсер XML RusLawOD → cleaner текста (чистка артефактов вида `<span class="cmd"/>`, лишних тегов) → чанкер (по «Статья N.», фолбэк — абзацы) → эмбеддинги bge-m3 (CPU; скачивание с официального HF, при блокировке — `HF_ENDPOINT` из `.env`) → upsert Qdrant (collection `chunks`, payload: act_id, title, chunk_no, clearance) → построение графа в Neo4j.

Особенности реальной схемы корпуса (проверено на corpus_test, 100 файлов):
- keywords: CSV в одном атрибуте `keywordsByIPS val="A, B, C"`; ~19% файлов пустые — обрабатывать отсутствие;
- классификатор: пары `код$Название$код$Название...` в атрибуте внутри обёртки `<reference>` (единственное число, НЕ `<references>` как в README); ~18% пустые;
- статусы требуют нормализации (встречается латинская «c» в «Действует c изменениями»);
- ссылки на другие акты `<ref nd="ID">` присутствуют в ~20% файлов.

Детерминированная часть графа (без LLM): `ISSUED_BY` ← doc_author_normal_formIPS; `REFERENCES` ← ref nd= (ребро создаётся только если целевой акт есть в корпусе); `HAS_TOPIC` ← классификатор/ключевые слова.
LLM-часть (GraphRAG): экстракция `(:Concept)` из текста чанков через vLLM → `(:Act)-[:MENTIONS]->(:Concept)`.
CLI-команда + `POST /admin/ingest` (background task). Разметка части актов как INTERNAL/SECRET.
**Acceptance:** после прогона в Neo4j Browser видны Act/Authority/Topic и связи; в Qdrant есть векторы с payload.
**Тесты:** unit (парсер, чанкер, маппинг онтологии), integration (реальные Qdrant/Neo4j из compose: узлы и векторы существуют), идемпотентность повторного прогона (нет дублей). Postman: ingest + статус.

### [ ] Этап 5. Query pipeline (LangGraph)
**Deliverables:** граф LangGraph (state machine, НЕ линейная цепочка):
- Узлы: `guardrail_in → planner (выбор инструментов) → tools [retrieve_vec (Qdrant+ACL filter) | expand_graph (Neo4j Cypher 1–2 hop)] → fusion → rerank (bge-reranker CPU) → generate (vLLM stream) → evaluate → guardrail_out (проверка цитат)`
- **Agent Loop:** после generate узел `evaluate` проверяет достаточность контекста; при нехватке — возврат к planner с уточнённым запросом (максимум 2 итерации)
- Компоненты по заданию: **Memory** (`agents/memory.py` — история сессии из PG в состояние графа), **Planner** (узел планирования инструментов), **Tools Interface** (`rag/tools.py`)
SSE-эндпоинт `POST /api/chat`, fallback-статусы `degraded`/`empty`. Контракт чата описывается в OpenAPI (в т.ч. SSE-формат событий) и экспортируется в `docs/api/openapi.yaml`.
**Acceptance:** вопрос → ответ с цитатами; в трейсе видны шаги графа включая цикл re-plan; стриминг работает.
**Тесты:** unit графа (mock LLM/retrievers, проверка переходов состояний И цикла re-plan), integration против стека, prompt-тесты LLM-as-a-Judge (маркер `gpu_slow`, отдельная команда make), тест формата SSE. Postman: чат (non-stream вариант для проверки структуры).

### [ ] Этап 6. Security RBAC сквозной
**Deliverables:** ACL-фильтр в Qdrant query (`clearance ∈ allowed(role)`) и WHERE-условие во всех Cypher. Резолв меток из JWT.
**Acceptance (критично!):** User B (viewer) не получает контент SECRET-акта ни в ответе, ни в цитатах, ни через расширение графа; User A (analyst) получает INTERNAL.
**Тесты:** негативные сценарии pytest (два пользователя × секретный документ), попытки обхода. Postman: RBAC-сценарий.

### [ ] Этап 7. Observability (полные три столпа)
**Deliverables:**
- **Трейсы:** OTel SDK + auto-instrumentation (FastAPI, httpx, SQLAlchemy) + ручные спаны на каждом узле LangGraph; экспорт в Jaeger через otel-collector; корреляция `X-Trace-Id`/`traceparent` от фронта через Traefik → backend → все внешние вызовы; Langfuse получает трейсы промптов (внешний URL из `.env`, отключаемо).
- **Метрики:** Prometheus-эндпоинт backend: RPS, latency-гистограммы per endpoint и per узел графа, счётчики ошибок/статусов (`degraded`/`empty`), размер контекста; дашборд Grafana (json в `infra/grafana/`): latency, RPS, tokens/sec с vLLM.
- **Логи:** структурный JSON-формат, уровни по env (`APP_LOG_LEVEL`), в каждой строке `trace_id`, `request_id`, `user_id`; логи запросов и результатов guardrails пишутся также в PG (audit_log) для демо.
**Acceptance:**
- после запроса в Jaeger виден полный трейс со спанами всех узлов графа и внешних вызовов;
- переданный клиентом `X-Trace-Id` виден в трейсе, эхо возвращается в ответе;
- Grafana показывает живые метрики при нагрузке; в логах каждая строка содержит тот же trace_id, что и трейс.
**Тесты:** unit middleware (генерация/проброс X-Trace-Id, инъекция в логи), integration «после запроса существует трейс (Jaeger API) и метрики обновились (/metrics)», тест что лог-строки парсятся как JSON и содержат trace_id. Postman: `/metrics`, проверка заголовков X-Trace-Id/X-Request-Id.

### [ ] Этап 8. Фронтенд React SPA (минимальный)
**Deliverables:** Vite + React + TS, без Redux/UI-китов — хуки + чистый CSS (тёмная тема). Экраны: Login (JWT), Chat (SSE-стриминг, цитаты, путь по графа «чипами»), Admin (кнопка ingestion + статус). nginx-контейнер за Traefik на `/`.
**Интеграция по контракту:** типы API генерируются из коммиченного `docs/api/openapi.yaml` (`npx openapi-typescript` → `src/lib/api-types.ts`, регенерация при изменении контракта); тонкий fetch-обёртка с подстановкой JWT и обработкой ошибок по схемам из контракта.
**Acceptance:** полный сценарий демо проходит в браузере: логин → вопрос → стриминг ответа → цитаты → путь графа; viewer не видит секретные источники; типы фронта соответствуют актуальному контракту.
**Тесты:** typecheck + eslint чистые; опционально vitest smoke на компоненты. Демо-сценарий фиксируется текстом.

### [ ] Этап 9. E2E Postman + нагрузочный отчёт
**Deliverables:** полная коллекция Postman (`tests/postman/`: env local, сценарии auth → ingest status → chat RBAC → health/metrics), прогон через Newman CLI `scripts/run_postman.(ps1|sh)`. Нагрузочный тест (locust или k6) на `/api/chat` (non-stream) и `/health` → `docs/load-report.md` (RPS, p50/p95, токены/сек на RTX 5070 Ti).
**Acceptance:** `make test-postman` зелёный; отчёт с цифрами готов.
**Тесты:** newman exit-code = gate; скрипт нагрузочного запуска воспроизводим.

### [ ] Этап 10. Minikube (последний этап)
**Deliverables:** `infra/helm/` (или сырые манифесты): Deployments, Services, ConfigMaps/Secrets, Ingress (Traefik addon или IngressRoute), PVC для PG/Qdrant/Neo4j, GPU-ресурсы на поде vLLM, подключение к внешнему Langfuse (host-docker). Здесь же снимается видео-демо.
**Acceptance:** весь флоу работает в minikube; тот же newman проходит против ingress-host.
**Тесты:** newman против minikube.

### [ ] Этап 11. Финализация
**Deliverables:** README (быстрый старт compose + minikube), видео-скрипт 5–7 мин (граф в Neo4j Browser, трейсы Jaeger/Langfuse, RBAC-демо), финальный полный прогон всех тестов, чистка `.env.example` от секретов.
**Acceptance:** репо самодостаточно для проверки преподавателем.

## Сводные правила тестирования

- Каждый этап завершается зелёным pytest перед коммитом.
- Маркеры pytest: `unit` / `integration` (нужен запущенный compose) / `gpu_slow` (LLM-as-a-Judge).
- Makefile-цели: `make test-unit`, `make test-integration`, `make test-postman`, `make test-all`.
- Коллекция Postman пополняется на этапах 3–7, полностью гоняется на этапах 9–10 (включая minikube).

## Критические критерии приёмки (из tasks.md)

1. Нет облачных API — всё локально.
2. Есть Deployment и Data Flow диаграммы.
3. Реализован GraphRAG (векторный поиск + расширение по графу), а не просто RAG.
4. RBAC реально работает (User B не получает секретный документ).
5. Control Plane (агенты/backend) отделён от Data Plane (БД/модели).
6. Оркестрация — LangGraph (state machine), не линейные скрипты.
7. Желательно: streaming, LLM-as-a-Judge тесты.
