# Data Flow — потоки данных платформы

Два основных потока: **ingestion** (загрузка корпуса) и **query** (вопросно-ответный поиск).
Все шаги трассируются через OTel (`X-Trace-Id`), секретные данные не покидают хранилища
без прохождения ACL-фильтра (ADR-007).

## 1. Поток Ingestion

```mermaid
flowchart TD
    A[XML-файлы RusLawOD<br/>corpus_test / полный корпус] --> B[Parser<br/>lxml + схема v3]
    B --> C[Cleaner<br/>чистка артефактов span/тегов,<br/>нормализация статусов]
    C --> D[Chunker<br/>разбивка по «Статья N.»<br/>фолбэк — абзацы]
    D --> E[Embedder bge-m3 CPU<br/>dense 1024]
    E --> F[(Qdrant<br/>collection chunks<br/>payload: act_id · title · chunk_no · clearance)]
    D --> G[Детерминированные рёбра<br/>из метаданных XML]
    D --> H[LLM-экстракция Concept<br/>vLLM · JSON-схема]
    G --> I[(Neo4j<br/>Act · Authority · Topic<br/>ISSUED_BY · REFERENCES · HAS_TOPIC)]
    H --> I
    F --> J[(PostgreSQL<br/>audit_log: ingestion run)]
    I --> J
```

Шаги:

1. **Parser** читает XML RusLawOD v3; извлекает метаданные: `doc_author_normal_formIPS`
   (издатель), классификатор (`код$Название$...` в `<reference>`), keywords (CSV),
   ссылки `<ref nd="ID">`, статус.
2. **Chunker** режет текст по заголовкам «Статья N.»; если статья слишком крупная или
   заголовков нет — фолбэк на абзацы (~512–1024 токена).
3. **Embedder** считает dense-вектора батчами (CPU).
4. **Qdrant upsert** — идемпотентный: повторный прогон обновляет точки по детерминированным ID.
5. **Граф**: детерминированные рёбра строятся без LLM (`REFERENCES` — только если цель есть в корпусе);
   `(:Concept)` извлекается LLM JSON-списком и пост-валидацией.
6. Каждый запуск пишется в `audit_log`; часть актов размечается INTERNAL/SECRET для демо RBAC.

## 2. Поток Query

```mermaid
sequenceDiagram
    participant U as Пользователь
    participant FE as Frontend SPA
    participant BE as Backend API
    participant Q as Qdrant
    participant N as Neo4j
    participant R as Reranker (CPU)
    participant V as vLLM

    U->>FE: вопрос (+ X-Trace-Id)
    FE->>BE: POST /api/chat (JWT)
    BE->>BE: guardrail_in (инъекции/PII) + Memory из PG
    BE->>Planner: план инструментов
    BE->>Q: retrieve_vec (filter clearance IN allowed)
    Q-->>BE: топ-K чанков [только разрешённые]
    BE->>N: expand_graph 1–2 hop (WHERE clearance IN allowed)
    N-->>BE: связанные акты/понятия
    BE->>R: fusion (RRF) + rerank
    R-->>BE: топ-N контекста
    BE->>V: generate (SSE stream)
    V-->>BE: токены
    BE->>BE: evaluate → re-plan ≤2 → guardrail_out (цитаты из разрешённых источников)
    BE-->>FE: SSE: ответ + цитаты + путь графа
    FE-->>U: отображение ответа
```

Ключевые свойства потока:

- **Pre-fetch ACL**: фильтр clearance применяется внутри запросов к Qdrant/Neo4j —
  запрещённые данные физически не попадают в контекст генерации.
- **Agent Loop**: после generate узел evaluate решает: достаточно ли контекста;
  при нехватке — возврат к planner с уточнением (максимум 2 итерации).
- **Цитаты**: формируются только из отфильтрованного контекста; guardrail_out проверяет
  соответствие цитат источникам.
- **Fallback**: деградация компонентов отражается статусами `degraded` / `empty`,
  а не ошибками пользователю.

## 3. Поток наблюдаемости

```mermaid
flowchart LR
    FE[Frontend] -- X-Trace-Id / traceparent --> T[Traefik]
    T --> BE[Backend FastAPI + LangGraph]
    BE -->|OTLP| OC[OTel Collector]
    OC --> J[(Jaeger)]
    OC --> P[(Prometheus)]
    P --> G[Grafana dashboards]
    BE -->|JSON logs + trace_id/user_id| L[stdout / audit_log PG]
    BE -.->|langfuse SDK, session = X-Trace-Id, отключаемо| LF[Langfuse self-hosted compose]
```

Правило разбора инцидента: `X-Trace-Id` из ответа API → спаны в Jaeger → логи с этим trace_id.
