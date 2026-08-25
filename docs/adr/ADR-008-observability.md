# ADR-008: Observability

- Статус: принято (дополнено на этапе 7)
- Дата: 2026-08-25
- Связанные: этап 7; требование задания «tracing через OTel»

## Контекст

Многошаговый агентный пайплайн (guardrails → planner → tools → rerank → generate → evaluate)
неотладочен без трассировки; задание требует OTel, Prometheus/Grafana. Нужны все три столпа:
трейсы + метрики + структурные логи со сквозной корреляцией от фронта (`X-Trace-Id`).

## Решение

- **Трейсы:** OpenTelemetry SDK в backend (instrumentation httpx/SQLAlchemy +
  ручные спаны на узлы LangGraph) → экспорт через **otel-collector** в **Jaeger**.
  Корреляция: клиент шлёт `X-Trace-Id` + W3C `traceparent`; Traefik пробрасывает; backend продолжает.
- **Метрики:** Prometheus скрейпит `/metrics` backend (RPS, latency-гистограммы per endpoint
  и per узел графа, статусы degraded/empty) и нативные метрики vLLM (tokens/sec).
  Дашборды Grafana — json-provisioning в `infra/grafana/`.
- **Логи:** структурный JSON, request-id middleware, `trace_id/request_id/user_id` в каждой записи;
  результаты guardrails и отказы доступа дублируются в `audit_log` (PG) для демо.
- **LLM-наблюдаемость:** **Langfuse self-hosted** — опциональный docker-compose стек
  (`infra/docker-compose.langfuse.yml`, проект `graphrag-langfuse`: web+worker+postgres+
  clickhouse+minio+redis), поднимается отдельно от ядра. Backend интегрируется через
  `langfuse-python` SDK: сессия = `X-Trace-Id`, промпты/комплиты/латентность генераций.
  Интеграция отключаемо (`APP_LANGFUSE_ENABLED=false` или пустой URL); недоступность Langfuse
  не влияет на чат (таймауты, flush best-effort, graceful skip).

## Trade-offs

| Задача | Выбор | Альтернатива и почему нет |
|--------|-------|---------------------------|
| Трейсы | Jaeger | Tempo — мощнее, но сложнее старт и запросы (TraceQL) для MVP |
| Метрики | Prometheus + Grafana (prometheus-client) | OTel Metrics API — единая точка, но лишний слой абстракции для MVP |
| Промпт-аналитика | Langfuse self-host (опц. compose) | Только логи — теряются сравнение версий промптов и стоимость |
| Интеграция Langfuse | langfuse-python SDK | Экспорт через otel-collector — ноль зависимостей в коде, но условное включение экспортёра и ручные gen_ai-атрибуты |
| Логи | JSON в stdout + audit_log в PG | Loki — плюс один сервис; отложено как расширение |

## Последствия

- Положительные: разбор любого запроса по цепочке «X-Trace-Id → спан в Jaeger → логи с этим id»
  (единый trace_id: серверный span создаётся из `X-Trace-Id`/`traceparent`, Jaeger trace ==
  значение в логах); дашборд для нагрузочного отчёта этапа 9 собирается из готовых метрик;
  Langfuse-стек не тянет за собой ядро — ядро работает без него.
- Отрицательные / риски:
  - otel-collector — лишний хоп, но даёт замену бэкенда без правок кода (пригодится в minikube);
  - langfuse-python SDK — зависимость в коде; компенсируется обёрткой с таймаутами,
    best-effort flush и флагом отключения.

## Дополнение (этап 7, по итогам выполнения)

- Единый trace_id реализован созданием серверного span прямо в CorrelationIdMiddleware:
  W3C-extract из `traceparent`; если заголовка нет — SpanContext собирается из `X-Trace-Id`
  (32-hex; произвольные значения хешируются sha256). FastAPI auto-instrumentation для
  входящих HTTP не используется, чтобы контролировать trace_id; httpx/SQLAlchemy инструментируются.
- Метрики — prometheus-client напрямую: HTTP-middleware (route template, исключая /health,/metrics)
  + бизнес-метрики чата (`chat_status_total`, guardrail-блокировки, длительность узлов графа).
- Langfuse-стек повторяет рабочий состав v3 на Redis (web+worker+postgres+clickhouse+minio+redis);
  web подключён к сети `graphrag_edge` (backend ходит на http://langfuse-web:3000), хост-порт
  только у web (APP_LANGFUSE_PORT, default 3300) — не конфликтует с независимо поднятым инстансом.
