# ADR-008: Observability

- Статус: принято
- Дата: 2026-08-25
- Связанные: этап 7; требование задания «tracing через OTel»

## Контекст

Многошаговый агентный пайплайн (guardrails → planner → tools → rerank → generate → evaluate)
неотладочен без трассировки; задание требует OTel, Prometheus/Grafana. Нужны все три столпа:
трейсы + метрики + структурные логи со сквозной корреляцией от фронта (`X-Trace-Id`).

## Решение

- **Трейсы:** OpenTelemetry SDK в backend (auto-instrumentation FastAPI/httpx/SQLAlchemy +
  ручные спаны на узлы LangGraph) → экспорт через **otel-collector** в **Jaeger**.
  Корреляция: клиент шлёт `X-Trace-Id` + W3C `traceparent`; Traefik пробрасывает; backend продолжает.
- **Метрики:** Prometheus скрейпит `/metrics` backend (RPS, latency-гистограммы per endpoint
  и per узел графа, статусы degraded/empty) и нативные метрики vLLM (tokens/sec).
  Дашборды Grafana — json-provisioning в `infra/grafana/`.
- **Логи:** структурный JSON, request-id middleware, `trace_id/request_id/user_id` в каждой записи.
- **LLM-наблюдаемость:** **Langfuse** — внешний docker-инстанс (URL из `.env`, интеграция отключаемо):
  промпты/комплиты/латентность генераций.

## Trade-offs

| Задача | Выбор | Альтернатива и почему нет |
|--------|-------|---------------------------|
| Трейсы | Jaeger | Tempo — мощнее, но сложнее старт и запросы (TraceQL) для MVP |
| Метрики | Prometheus + Grafana | Стандарт де-факто, требуется заданием |
| Промпт-аналитика | Langfuse self-host | Только логи — теряются сравнение версий промптов и стоимость |
| Логи | JSON в stdout + audit_log в PG | Loki — плюс один сервис; отложено как расширение |

## Последствия

- Положительные: разбор любого запроса по цепочке «X-Trace-Id → спан в Jaeger → логи с этим id»;
  дашборд для нагрузочного отчёта этапа 9 собирается из готовых метрик.
- Отрицательные / риски:
  - otel-collector — лишний хоп, но даёт замену бэкенда без правок кода (пригодится в minikube);
  - Langfuse внешний: его недоступность не должна ломать чат — клиент с таймаутом и graceful skip.
