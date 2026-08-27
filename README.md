# GraphRAG-платформа корпоративных знаний

Дипломный проект OTUS 2026: защищённая платформа вопросно-ответного поиска по корпусу
правовых документов в **закрытом контуре** (on-premise, без внешних LLM API).

- **GraphRAG**, а не просто RAG: гибридный retrieval — векторный поиск (Qdrant) + расширение по графу знаний (Neo4j);
  граф строится гибридно: детерминированные рёбра из метаданных XML + LLM-экстракция понятий (`:Concept`).
- **Agent Loop на LangGraph** (state machine, не линейная цепочка): guardrails → planner → tools → fusion/rerank → generate → evaluate → re-plan (≤2 итераций).
- **Security-by-Design**: RBAC с pre-fetch ACL-фильтрацией — секретный документ физически не покидает хранилище (проверено негативными E2E и Postman-сценариями).
- **Наблюдаемость**: OTel-трейсы (Jaeger, Langfuse), метрики (Prometheus/Grafana), структурные JSON-логи; сквозная корреляция `X-Trace-Id` от браузера.
- **Стриминг** ответа пользователю (SSE) с интерактивными цитатами `[S#]` и визуализацией пути по графу.

## Соответствие заданию (tasks.md)

| Требование | Артефакт |
|---|---|
| C4 L1/L2/L3, Deployment, Sequence, ER | `docs/architecture/*.drawio` (обзор — [docs/index.md](docs/index.md)) |
| Data Flow | [docs/architecture/dataflow.md](docs/architecture/dataflow.md) |
| ADR с trade-off анализом (LLM Serving, модель, Qdrant, Neo4j, LangGraph, GraphRAG, RBAC, Observability, Embeddings, Deployment, РФ-доступность) | [docs/adr/ADR-001..011](docs/index.md) |
| GraphRAG (вектор + граф, гибридная экстракция) | ADR-006, `backend/app/rag/`, `backend/app/ingestion/` |
| LangGraph state machine (Memory / Planner / Tools) | ADR-005, `backend/app/agents/graph.py` |
| RBAC: User B не получает секретный документ | ADR-007, этап 6 PLAN.md, Postman-папка `40-chat-rbac` |
| Streaming (SSE) | `POST /api/chat`, SSE-события в `docs/api/openapi.yaml` |
| Цикл запроса в чате: конвейер агента, панели Grafana, разбор трейсов Jaeger | [docs/query-flow-deep-dive.md](docs/query-flow-deep-dive.md) |
| LLM-as-a-Judge тесты промптов | `make test-judge` (маркер `gpu_slow`) |
| Нагрузочный отчёт (RPS, латентность, tok/s) | [docs/load-report.md](docs/load-report.md) |
| Видео-демо 5–7 мин «под капотом» | [docs/video-demo-script.md](docs/video-demo-script.md) |

> **Скоуп:** по заданию реализуется одна тема из раздела Implementation — выбрана
> **Advanced RAG → GraphRAG** (Knowledge Graphs). Мультимодальность (сканы/чертежи/PDF)
> из шапки задания в MVP не входит: корпус — XML-датасет RusLawOD v3, конвейер и
> Security-by-Design демонстрируются на нём. Дополнительно перекрыта тема Security
> (Input/Output Guardrails: санитайзер + детекция инъекций + проверка цитат).

## Быстрый старт с нуля (docker compose)

Предпосылки: Docker Desktop (WSL2), Python 3.11+ с [uv](https://docs.astral.sh/uv/)
(сидинг и скрипты), LM Studio на хосте с загруженной Qwen-моделью (dev-LLM; целевой
движок vLLM — `infra/docker-compose.gpu.yml`, ADR-001).

```powershell
# 1. Конфиг окружения (дев-пароли уже внутри, реальный .env не коммитится)
Copy-Item infra\.env.example infra\.env

# 2. Корпус: тестовый corpus_test/ (100 XML, RusLawOD v3) положить в корень репозитория
#    https://github.com/irlcode/RusLawOD (свой каталог — CORPUS_HOST_DIR в infra/.env)

# 3. Поднять стек: traefik, backend, frontend, postgres, qdrant, neo4j, vault
docker compose -f infra/docker-compose.yml up -d

# 4. Наблюдаемость (отдельный проект): Jaeger :16686, Prometheus :9090, Grafana :3000
docker compose -f infra/docker-compose.observability.yml up -d

# 5. Демо-пользователи (dev-пароли, см. ниже)
make seed-users

# 6. Проверка окружения (таблица OK/FAIL; строка llm — зелёная при запущенном LM Studio)
python scripts\smoke_infra.py --with-obs

# 7. Наполнение базы: UI http://localhost → Admin → «Запустить ingestion»
#    (первый полный прогон corpus_test ~10–30 мин на CPU; далее — инкрементальный, секунды)
```

Затем открыть **http://localhost**, войти и задать вопрос по корпусу. Первый чат/прогон
медленнее: backend докачивает модели эмбеддингов в кэш (~4.5 GB, урок этапа 5).

| Пользователь (dev) | Пароль | Грифы |
|---|---|---|
| `viewer` | `viewer123` | PUBLIC |
| `analyst` | `analyst123` | PUBLIC, INTERNAL |
| `admin` | `admin123` | все + Admin UI |

Полезные входы: Swagger — http://api.localhost/docs · Neo4j Browser — http://neo4j.localhost
· Jaeger — http://127.0.0.1:16686 · Grafana — http://127.0.0.1:3000.

## Режимы запуска

| Режим | Команда | Документация |
|---|---|---|
| **Compose** (разработка) | `docker compose -f infra/docker-compose.yml up -d` | [infra/README.md](infra/README.md) |
| **Minikube/Helm** (целевой, этап 10) | `make k8s-up` → http://localhost:8080 | [docs/minikube-deployment.md](docs/minikube-deployment.md) |

Быстрый старт в minikube (предпосылки и полный runbook — в
[docs/minikube-deployment.md](docs/minikube-deployment.md)):

```powershell
make k8s-up          # кластер + helm upgrade + port-forward + статус
# UI: http://localhost:8080   Swagger: http://localhost:8080/docs
make test-postman POSTMAN_ARGS="-BaseUrl http://127.0.0.1:8080"   # E2E gate против кластера
make k8s-down        # остановить port-forward (+ -StopCluster для minikube stop)
```

## Тестирование

Пирамида, последовательности, гигиена данных: [tests/README.md](tests/README.md).
Коротко: `make lint` → `make test-unit` → `make test-integration` → `make test-postman`
(для последних двух нужен поднятый стек и LM Studio). Дополнительно: `make test-judge`
(LLM-as-a-Judge), `make load-test` / `make bench-llm` (нагрузка, цифры — в load-report).

## Работа с корпусом (пополнение документов)

Ingestion **инкрементальный**: прогон обрабатывает только новые/изменившиеся файлы
(снапшот по sha256 в PG), неизменённые пропускает — повторный запуск занимает секунды,
первый полный прогон корпуса 10–30 мин. Любой способ пополнения заканчивается
кнопкой «Запустить ingestion» в Admin UI (или `POST /admin/ingest`):

| Способ | Где работает | Как |
|---|---|---|
| Папка на хосте | compose | скопировать XML в `CORPUS_HOST_DIR` (по умолчанию `corpus_test/`) — маунт живой |
| Upload через UI/API | compose + minikube | Admin → «Загрузка файлов» (`POST /admin/documents`, admin, только `.xml`); удаление — `DELETE /admin/documents/{filename}` |
| kubectl cp | minikube | `kubectl cp act.xml graphrag/backend-<pod>:/data/corpus/` |

Полный пересчёт всего корпуса (после смены чанкера/модели эмбеддингов/процентов
грифа) — кнопка «Полный пересчёт» или `POST /admin/ingest?full=true` / CLI `--full`.
Подробнее: `backend/README.md` (правила), `infra/README.md` (compose),
`docs/minikube-deployment.md` (кластер).

## Карта репозитория

```
/infra      docker-compose (+gpu, observability, langfuse), traefik/grafana, helm/graphrag
/backend    FastAPI + LangGraph-агент: agents/ (graph, planner, memory, guardrails),
            rag/ (retrievers, fusion, reranker, tools), ingestion/, llm/, db/, observability/
/frontend   React SPA (Vite + TS, без UI-китов), nginx-контейнер за traefik
/docs       ADD: index, ADR-001..011, architecture/*.drawio, dataflow, load-report,
            minikube-deployment, video-demo-script, api/openapi.yaml
/tests      Postman-коллекция (newman gate: make test-postman)
/scripts    seed_users, smoke_infra, export_openapi, run_postman, run_load_test, k8s_*
corpus_test/  тестовый корпус RusLawOD (не в git — скачать, см. быстрый старт)
```
