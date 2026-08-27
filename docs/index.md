# GraphRAG-платформа корпоративных знаний — Архитектурная документация

MVP платформы GraphRAG-анализа корпоративных знаний в закрытом контуре (on-premise, без внешних LLM API).
Датасет — RusLawOD v3 (XML правовых актов РФ). Реализация темы **Advanced RAG → GraphRAG**.

## Цели системы

1. E2E конвейер: ingestion XML-корпуса → чанки + векторы + граф знаний → вопросно-ответный поиск с цитатами.
2. Нейро-символический GraphRAG: детерминированные рёбра из метаданных + LLM-экстракция понятий.
3. Security-by-Design: RBAC с pre-fetch фильтрацией (User B не видит SECRET документ нигде).
4. Полная наблюдаемость: трейсы + метрики + логи, сквозная корреляция `X-Trace-Id`.

## Ключевые архитектурные решения

| # | Компонент | Решение | ADR |
|---|-----------|---------|-----|
| 1 | LLM Serving | vLLM (OpenAI-compatible API) | [ADR-001](adr/ADR-001-llm-serving.md) |
| 2 | Модель | Qwen3-8B-AWQ (4-bit) | [ADR-002](adr/ADR-002-model.md) |
| 3 | Vector DB | Qdrant | [ADR-003](adr/ADR-003-vector-db.md) |
| 4 | Graph DB | Neo4j Community | [ADR-004](adr/ADR-004-graph-db.md) |
| 5 | Оркестрация | LangGraph | [ADR-005](adr/ADR-005-orchestration.md) |
| 6 | GraphRAG-подход | Гибридная экстракция графа | [ADR-006](adr/ADR-006-graphrag.md) |
| 7 | Security/RBAC | JWT от backend + ACL pre-fetch | [ADR-007](adr/ADR-007-security-rbac.md) |
| 8 | Observability | OTel → Jaeger/Prometheus/Grafana; Langfuse | [ADR-008](adr/ADR-008-observability.md) |
| 9 | Embeddings | bge-m3 + bge-reranker-v2-m3 (CPU) | [ADR-009](adr/ADR-009-embeddings.md) |
| 10 | Deployment | docker-compose (dev) → minikube/Helm (target) | [ADR-010](adr/ADR-010-deployment.md) |
| 11 | РФ-доступность | Официальные источники; план Б — зеркала через env | [ADR-011](adr/ADR-011-rf-accessibility.md) |

## Диаграммы (C4 model)

| Диаграмма | Файл | Содержание |
|-----------|------|------------|
| C4 L1 Context | [c4-l1-context.drawio](architecture/c4-l1-context.drawio) | Пользователи/роли, система как чёрный ящик, ERP, CRM, User Channels |
| C4 L2 Container | [c4-l2-container.drawio](architecture/c4-l2-container.drawio) | Контейнеры системы; Control Plane vs Data Plane |
| C4 L3 Agent Component | [c4-l3-agent-component.drawio](architecture/c4-l3-agent-component.drawio) | Memory module, Planner, Tools Interface, guardrails, retrievers |
| Deployment | [deployment.drawio](architecture/deployment.drawio) | Зоны DMZ / Internal / GPU, Vault, балансировка |
| Sequence | [sequence-query.drawio](architecture/sequence-query.drawio) | Поток запроса: guardrails → agent loop → tool execution → response |
| ER-модель | [er-model.drawio](architecture/er-model.drawio) | Таблицы PG, коллекция Qdrant, граф Neo4j |
| Data Flow | [dataflow.md](architecture/dataflow.md) | Потоки данных: ingestion и query |

## API

Контракт API: `api/openapi.yaml` (создаётся на этапе 3). Swagger UI — `/docs` за Traefik.

## Глубокие погружения

- [Цикл запроса в чате](query-flow-deep-dive.md) — конвейер агента по узлам (что/почему/профит),
  разбор панелей Grafana и реальных трейсов Jaeger (happy path, re-plan ×2, отражённая инъекция).
- [Видео-демо: сценарий](video-demo-script.md) — архитектурный нарратив защиты (5–7 мин).
- [Нагрузочный отчёт](load-report.md) — методика и цифры RPS/латентности/tok/s.

## Глоссарий

| Термин | Значение |
|--------|----------|
| RAG | Retrieval-Augmented Generation — генерация с дополнением из базы знаний |
| GraphRAG | RAG, где retrieval использует граф знаний (сущности и связи), а не только векторный поиск |
| Chunk | Фрагмент документа (≈ статья акта), единица индексации в Qdrant |
| Clearance | Метка уровня доступа документа: `PUBLIC` / `INTERNAL` / `SECRET` |
| ACL pre-fetch | Фильтрация по clearance ДО выполнения поиска — секрет не покидает хранилище |
| Control Plane | Компоненты управления трафиком и оркестрации: Traefik, Backend/FastAPI, Frontend |
| Data Plane | Компоненты обработки данных: vLLM, Qdrant, Neo4j, PostgreSQL |
| Agent Loop | Цикл LangGraph: planner → tools → generate → evaluate → (re-plan, макс. 2 итерации) |
| Planner | Узел графа, выбирающий инструменты и стратегию для текущего шага |
| Tools Interface | Единый интерфейс инструментов агента: retrieve_vec, expand_graph |
| Guardrails | Проверки входа (инъекции, PII) и выхода (цитаты только из разрешённых источников) |
| Re-plan | Возврат к planner при недостаточности контекста после generate |
| AWQ | 4-bit weight-only квантование активационно-осведомлённое |
| KV-cache | Кэш ключей/значений внимания; основной потребитель VRAM помимо весов |
| OTel | OpenTelemetry — стандарт трассировки/метрик |
| X-Trace-Id | Заголовок сквозной корреляции запроса от клиента до всех сервисов |
| MENTIONS | Ребро графа `(:Act)-[:MENTIONS]->(:Concept)` из LLM-экстракции |
| RusLawOD | Открытый корпус XML правовых актов РФ (1991–2025) |
