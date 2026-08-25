# Postman / Newman (E2E, этапы 3–9)

Единая коллекция — `graphrag.postman_collection.json`, окружение — `env.local.json`.
Источник правды по контракту — `docs/api/openapi.yaml` (экспорт из FastAPI:
`make openapi-export`); при изменении эндпоинтов коллекция дополняется вручную
по контракту.

## Состав коллекции

| Папка | Что проверяет |
|---|---|
| `00-system` | `/health`, эхо `X-Trace-Id`/`X-Request-Id`, `/metrics` (семейства метрик) |
| `10-auth` | логин admin, ошибки аутентификации (401 + коды), `/auth/me` |
| `20-users` | управление учётками: создание viewer/analyst, 409/422, смена роли, эскалация → 403 |
| `30-ingest` | статус ingestion (read-only), 401 анонима, 403 у viewer'а |
| `40-chat-rbac` | чат non-stream/SSE, память диалога, ACL на цитатах, injection, чужая сессия |
| `ingest-run` | **ОПЦИЯ**: запуск `POST /admin/ingest` (полный прогон корпуса, CPU 10–30 мин). В gate НЕ входит |

## Запуск

```powershell
make test-postman                                   # gate: newman против http://api.localhost (Traefik)
powershell -File scripts/run_postman.ps1 -BaseUrl "http://127.0.0.1:8000"   # мимо Traefik
sh scripts/run_postman.sh http://127.0.0.1:8000     # то же под sh
INGEST_RUN=1 sh scripts/run_postman.sh              # + опциональный запуск ingestion
```

Exit-code newman'а — гейт: `0` = зелёный. Таймаут запроса — 180 c
(чат-конвейер с LLM медленный, зависание не подвешивает прогон).
JSON-отчёт прогона пишется в `scripts/load_test/results/` (вне git).

Предусловия: стек поднят (`docker compose up -d`), сидированы пользователи
(`make seed-users`: viewer/viewer123, analyst/analyst123, admin/admin123),
корpus загружен в Qdrant/Neo4j (иначе чат-проверки на цитаты упадут).

## Нагрузочное тестирование (этап 9)

Отдельные скрипты: `scripts/run_load_test.ps1|.sh` (locust) и
`scripts/load_test/bench_llm.py` (tokens/sec движка LLM). См. `docs/load-report.md`.
