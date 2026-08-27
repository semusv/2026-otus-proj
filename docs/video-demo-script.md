# Видео-демо 5–7 мин — «под капотом» (защита курса «Архитектор ИИ»)

> Нарратив: показываем **не продукт, а архитектуру и её подтверждение в рантайме**.
> Формула каждого блока: *какое решение → почему (trade-off) → доказательство на живой системе*.
> Продуктовые фичи (стриминг, цитаты) — только как иллюстрация архитектурных решений.

## Подготовка к записи (чек-лист)

- [ ] Стек поднят: compose + observability (`docker compose -f infra/docker-compose.yml up -d`
      + `docker compose -f infra/docker-compose.observability.yml up -d`), smoke зелёный.
- [ ] LM Studio запущен с моделью (`APP_LLM_MODEL`); ingestion выполнен
      (`/admin/stats` показывает данные); **сделан 1–2 «прогревочных» чата**
      (первый чат докачивает bge-m3/reranker ~4.5 GB — в кадр не брать).
- [ ] Вкладки заранее (в порядке съёмки): `docs/architecture/c4-l1-context.drawio` →
      `c4-l2-container.drawio` → `c4-l3-agent-component.drawio` → `docs/index.md`
      (таблица ADR) → UI http://localhost → Jaeger http://127.0.0.1:16686 →
      Langfuse http://langfuse.localhost → Neo4j http://neo4j.localhost →
      Grafana http://127.0.0.1:3000 → `docs/load-report.md` → UI :8080 (minikube).
- [ ] Вопросы и Cypher из этого файла скопированы в буфер/файл.
- [ ] 1920×1080, зум браузера 110–125%, тёмная тема UI, звук без эха.
- [ ] Minikube-сегмент: кластер поднят заранее (`make k8s-up`), port-forward :8080 живой.

## Сценарий по минутам

### 0:00–0:30 — Интро и архитектурные драйверы (экран: C4 L1)
**Говорим:** задача — MVP конвейера извлечения знаний в закрытом контуре (on-premise,
без внешних LLM API). Три драйвера, определивших архитектуру: **GraphRAG** (борьба с
галлюцинациями через граф), **Security-by-Design** (RBAC на уровне чанков/узлов),
**наблюдаемость** (сквозная корреляция запроса). На диаграмме: пользователи/роли,
система как чёрный ящик, ERP/CRM/User Channels — L1 фиксирует границы ответственности.

### 0:30–1:15 — C4 L2: контейнеры, Control/Data Plane (экран: c4-l2-container)
**Говорим:**
- Control Plane: Traefik (шлюз) → Backend/LangGraph-оркестратор → Frontend SPA.
- Data Plane: Qdrant, Neo4j, PostgreSQL, LLM Serving (vLLM/LM Studio) — данные не
  смешиваются с оркестрацией, базы публикуют порты только на 127.0.0.1.
- Наблюдаемость — боковое ответвление (OTel → Jaeger/Prometheus/Grafana, Langfuse).
- Секреты — Vault (в k8s: initContainer читает KV, ADR-010).

### 1:15–2:00 — C4 L3: агент изнутри (экран: c4-l3-agent-component)
**Говорим:** ядро — **state machine на LangGraph, не линейная цепочка**:
`guardrail_in → planner → tools (retrieve_vec | expand_graph) → fusion/rerank →
generate → evaluate → guardrail_out`, и **цикл re-plan** (≤2 итераций): evaluate
оценивает достаточность контекста и возвращает планировщику уточнённый запрос.
Компоненты по заданию: **Memory** (история сессии из PG), **Planner** (гибрид:
правила + LLM rewrite), **Tools Interface** (реестр инструментов). Guardrails
входа/выхода — на границах контура агента.

### 2:00–2:45 — Ключевые ADR trade-offs (экран: docs/index.md, таблица решений)
Быстро, по ~8–10 сек на решение (детали — в самих ADR):
1. **ADR-001/002:** vLLM + Qwen3-8B-**AWQ**: квантование обязательно на consumer GPU;
   бюджет 16 GB — веса ~6 GB, остальное KV-cache; целевой движок vLLM, dev — LM Studio.
2. **ADR-003:** Qdrant — payload-фильтры для ACL pre-fetch.
3. **ADR-004:** Neo4j Community — зрелый Cypher для 1–2 hop расширения.
4. **ADR-005:** LangGraph — персистентный стейт, условные переходы (re-plan).
5. **ADR-006:** **GraphRAG гибридной экстракции**: детерминированные рёбра из метаданных
   (ISSUED_BY/REFERENCES/HAS_TOPIC — дёшево и воспроизводимо) + LLM-экстракция Concept/MENTIONS
   (нейро-символический подход). Онтология сознательно из 4 типов узлов.
6. **ADR-009:** bge-m3 + reranker CPU-in-process — ноль VRAM для эмбеддингов.

### 2:45–3:45 — Security-by-Design: решение + доказательство (экран: UI)
**Говорим (архитектура):** секрет не может утечь, потому что **не попадает в контекст**:
- pre-fetch фильтрация ДО retrieval — ACL-метка в payload Qdrant и на узлах графа,
  запрос выполняется только среди чанков/узлов разрешённых клейренсов роли (не пост-фильтрация ответа);
- defense-in-depth: инвариант в guardrail_out сверяет clearance каждого источника —
  даже при ошибке ретривера источник дропается, статус degraded;
- отказы (401/403/injection) пишутся в audit_log.
**Делаем (доказательство):** логин `analyst/analyst123` → вопрос
`«Какие нормативные документы есть по теме исполнения обязательств? Перечисли источники.»`
— есть INTERNAL-цитаты. Затем `viewer/viewer123` → тот же вопрос — INTERNAL/SECRET
источников **нет нигде** (ни в ответе, ни в цитатах, ни в пути графа).

### 3:45–4:45 — Архитектура в рантайме: трейс = граф агента (экран: Jaeger)
**Делаем:** из ответа чата копируем **trace_id** → Jaeger → открываем трейс.
**Говорим:** каждый узел state machine — отдельный спан: видна вся топология агента,
внешние вызовы (LLM, Qdrant, Neo4j, SQL) вложены в тот же трейс; если evaluate
вернул re-plan — в трейсе виден цикл planner/tools ×2. Правило отладки системы:
`X-Trace-Id из ответа → трейс в Jaeger → логи с этим trace_id`. Клиентский
trace_id приходит из браузера через Traefik и продолжается сервером (W3C traceparent).
**Langfuse (20 сек):** те же вызовы как промпт/комплит генерации, session = trace_id —
виден сам промпт с чанками и путём графа.

### 4:45–5:25 — Данные GraphRAG: граф и векторы (экран: Neo4j Browser)
**Делаем (Cypher, corpus_test = 100 актов):**
```cypher
MATCH p=(a:Act)-[:ISSUED_BY]->(x) RETURN p LIMIT 40
MATCH p=(a:Act)-[:MENTIONS]->(c:Concept)<-[:MENTIONS]-(b:Act) RETURN p LIMIT 30
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS cnt ORDER BY cnt DESC
```
**Говорим:** онтология Act/Authority/Topic/Concept; MENTIONS-рёбра построены LLM при
ingestion (~7000 на корпусе) — связи «через понятие» дают графовое расширение там,
где явных ссылок нет; в Qdrant каждый чанк несёт payload (act_id, clearance, chunk_no) —
это и есть точка pre-fetch фильтра.

### 5:25–6:00 — Нагрузка и наблюдаемость (экран: Grafana → load-report.md)
**Делаем:** Grafana-дашборд (RPS, latency, статусы чата, узлы графа).
**Говорим (цифры с RTX 5070 Ti, из load-report):** лёгкие эндпоинты ~220 RPS (p95=11ms);
полный ход агента p50 ≈ 7 c (1 пользователь) / ≈ 17 c (4 параллельно) — определяющий
фактор LLM-движок: 258 tok/s последовательно → 389 tok/s при 4 воркерах. Полный отчёт
с методикой — `docs/load-report.md`.

### 6:00–6:35 — Deployment: dev → целевой (экран: UI :8080)
**Говорим:** две целевые конфигурации из ADR-010: dev — docker-compose (сегментация
сетями edge/data), целевая — **minikube/Helm**: тот же образ backend, но секреты из
Vault KV (initContainer), ingress с аннотациями для SSE, LLM-движок вне кластера
(`host.minikube.internal`), наблюдаемость на хосте — один и тот же бэкенд работает
в обоих режимах без правок кода. **Делаем:** http://localhost:8080 — тот же чат
против кластера; E2E-gate newman проходит против ingress.

### 6:35–7:00 — Качество и финал (экран: README / tests/README)
**Говорим:** пирамида тестов — 122 unit (переходы графа, guardrails на фейках) →
184 integration (реальные Qdrant/Neo4j/PG, негативные RBAC-сценарии «обхода») →
newman gate (99 assertions) → LLM-as-a-Judge на промпты (`make test-judge`).
Репозиторий самодостаточен: быстрый старт с нуля в README, все решения — в ADR-001..011.

## Формулировка про vLLM (требование «логи vLLM»)

В сегменте 3:45–4:45 или 2:00–2:45 сказать явно: *«LLM-движок сменяем одной переменной
`APP_LLM_BASE_URL`: в записи — dev-профиль LM Studio (совместимый OpenAI API, его логи
видны в трейсе как llm-спаны), целевой профиль — vLLM с Qwen3-8B-AWQ
(`infra/docker-compose.gpu.yml`), его нативные метрики tokens/sec заведены в Grafana
и показаны в нагрузочном отчёте»*.

## Запасные тезисы (вопросы после видео)

- **Почему не Keycloak:** отдельный сервис авторизации для MVP избыточен; JWT выпускает
  сам backend, сессии/аудит в PG; Keycloak зафиксирован как rejected alternative (ADR-007).
- **Почему эмбеддинги на CPU:** 16 GB VRAM отданы генерации; bge-m3 на CPU не в
  критическом пути рантайма (query-encode ~десятки мс), зато VRAM-бюджет предсказуем (ADR-009).
- **Почему 4 типа узлов:** минимальная онтология по рекомендации задания; расширяется
  без ломки схемы (MERGE идемпотентен) — усложнение осознанное, не случайное (ADR-006).
- **Масштабирование:** backend stateless (стейт в PG) → реплики за шлюзом; GPU-зона
  вынесена на Deployment-диаграмме; узкое место — LLM-движок (batching vLLM).
- **Почему minikube, а не «боевой» k8s:** целевой Helm-чарт демонстрирует k8s-специфику
  (Vault, ingress, PVC, probes); миграция в кластер — замена ingress-класса и реестра образов.
