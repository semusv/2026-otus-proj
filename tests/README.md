# Запуск тестов GraphRAG Platform

Практический гайд: что гонять, в каком порядке, что для этого нужно.
Контракт API — `docs/api/openapi.yaml`; детали по уровням — `backend/README.md`
(pytest) и `tests/postman/README.md` (коллекция Newman).

## Пирамида тестов

| Уровень | Команда (из корня) | Что нужно | Время | Что проверяет |
|---|---|---|---|---|
| 1. Статический анализ | `make lint` | ничего (uv) | ~30 с | ruff + mypy — **gate каждого коммита** |
| 2. Unit | `make test-unit` | ничего (uv) | ~15–20 с | логика без внешних зависимостей (маркер `unit`, таймаут 120 с/тест) |
| 3. Integration | `make test-integration` | compose: postgres, qdrant, neo4j | ~2 мин | реальный PG/Qdrant/Neo4j: auth-флоу, ingestion (+инкрементальный), RBAC (таймаут 300 с/тест) |
| 4. Frontend | `make frontend-lint` → `make frontend-test` → `make frontend-build` | node_modules (`make frontend-install`) | ~1 мин | eslint, tsc --noEmit, vitest smoke (19), production build |
| 5. E2E (Newman) | `make test-postman` | **весь стек** + LM Studio с моделью | ~1.5–3 мин | сквозные сценарии через Traefik: чат, RBAC, upload, observability |
| 6. LLM-as-a-Judge (opt) | `make test-judge` | LM Studio + не-thinking модель | минуты | качество промптов/ответов (маркер `gpu_slow`) |
| 7. Нагрузка (opt) | `make load-test`, `make bench-llm` | стек + LM Studio | 5–15 мин | RPS/латентность/tokens/sec → `docs/load-report.md` |

## Предварительные требования

```powershell
uv sync                     # после git pull (обновился uv.lock) — один раз
make frontend-install       # node_modules для фронта — один раз

# стек для integration (достаточно трёх БД) и E2E (весь стек):
docker compose -f infra/docker-compose.yml up -d          # весь стек
# или минимум для integration:
docker compose -f infra/docker-compose.yml up -d postgres qdrant neo4j

# для E2E/Judge/нагрузки ещё нужен LLM: LM Studio на хосте (:1234) с загруженной моделью
# проверка: python scripts\smoke_infra.py --with-obs   (таблица OK/FAIL/SKIP)
```

## Последовательности

### Быстрая петля при разработке (каждая итерация)

```powershell
make lint && make test-unit
```

### Полный gate перед коммитом (правило PLAN.md: зелёные тесты = триггер коммита)

```powershell
make lint; if ($?) { make test-unit }
# правки фронта или контракта:
make frontend-lint; make frontend-test; make frontend-build
# контракт менялся? синхронизировать и закоммитить openapi.yaml + api-types.ts:
make openapi-export; make openapi-types
```

### E2E против compose (перед пушем / после изменений API)

```powershell
docker compose -f infra/docker-compose.yml up -d     # стек зелёный
make test-postman                                    # gate: exit-code newman'а
# полный прогон ingestion в коллекции - только явно (CPU 10–30 мин!):
make test-postman POSTMAN_ARGS="-IngestRun"
```

### E2E против minikube (этап 10)

```powershell
make k8s-up                                          # кластер + helm + port-forward :8080
make test-postman POSTMAN_ARGS="-BaseUrl http://127.0.0.1:8080"
make k8s-down
```

### Прогон всего подряд (приёмка этапа)

```powershell
make lint; if ($?) { make test-all }; if ($?) { make test-postman }
# + judge при работающем LM Studio:
make test-judge
```

## Маркеры pytest (backend)

| Маркер | Что | Запуск |
|---|---|---|
| `unit` | быстро, без сети/БД; LLM/эмбеддинги — стабы | `make test-unit` |
| `integration` | реальные PG/Qdrant/Neo4j из compose; БД `graphrag_itg_*` создаётся на сессию и удаляется | `make test-integration` (--timeout=300) |
| `gpu_slow` | LLM-as-a-Judge против живого LM Studio | `make test-judge` |

Таймауты: pytest-timeout — unit 120 с/тест (по умолчанию), integration 300 с/тест
(в Makefile-цели). Тест не может «зависнуть навсегда» — упадёт по таймауту.

## Гигиена тестовых данных

- Интеграционные тесты изолированы: свежая БД `graphrag_itg_<hex>` на сессию,
  отдельная коллекция Qdrant `chunks_test`, граф чистится до/после — основная
  БД `graphrag` и коллекция `chunks` не затрагиваются;
- Newman создаёт `qa_*/hacker_*` пользователей в dev-БД (разовая чистка —
  `scripts/cleanup_test_users.sql`); папка `35-documents` удаляет свои файлы
  корпуса в конце папки;
- Тесты ingestion не используют реальные ID корпуса — только синтетические
  (`900000101`, `999000001`, …);
- Тяжёлые/деструктивные сценарии (запуск ingestion) — в opt-in папке
  `ingest-run OPTIONAL full ingestion`, в gate не входят.

## Типичные проблемы

| Симптом | Причина / лечение |
|---|---|
| Integration падают «connection refused» | Не поднят compose: `docker compose -f infra/docker-compose.yml up -d postgres qdrant neo4j` |
| Newman: чат-запросы падают / пустые ответы | LM Studio не запущен или модель не загружена; «думающие» модели отдают пустой content — не-thinking модель в `APP_LLM_MODEL` |
| Newman: 401 в отдельных папках при точечном прогоне | Токены ставятся логинами внутри каждой папки/смежных — гоняйте полный gate `make test-postman`, а не одиночную папку |
| `docker build` «висит» долго | Проверьте `docker info`; лог билда пишите в файл (`*> build.log`), прогресс — в конце файла. После длинных билдов Docker Desktop изредка клинит — перезапуск Desktop возвращает стек |
| Первый чат/E2E «тишина» минуты | Контейнер качает bge-m3 + reranker (~4.5GB) при первом старте — прогрев:volume `hf_cache` уже настроен; дождитесь, повторный старт быстрый |

Подробности по нагрузочному стенду — `docs/load-report.md`, по minikube —
`docs/minikube-deployment.md`, по ingestion/корпусу — `backend/README.md`.
