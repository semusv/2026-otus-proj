# Проект
Построение защищенной платформы мультимодального анализа корпоративных знаний с использованием графовых подходов.

## Цель:
В проектной работе вам нужно спроектировать и реализовать MVP производственного конвейера (End-to-End Pipeline) для извлечения знаний из неструктурированных данных (сканы, чертежи, сложные PDF) в закрытом контуре.

Вы выступаете как Platform Engineer & AI Architect. Вы должны решить проблему «галлюцинаций» и «потери контекста» через внедрение GraphRAG (нейро-символический подход) и обеспечить Security-by-Design (разграничение прав доступа к данным на уровне чанков/узлов графа). Проект демонстрирует готовность к внедрению AI в банковский или промышленный сектор РФ.


## Описание/Пошаговая инструкция выполнения домашнего задания:

### Задача:


Вам поручено разработать архитектуру и MVP ядра корпоративной интеллектуальной системы. Система должна работать полностью автономно (On-premise / Air-gapped), без доступа к внешним API (OpenAI/Anthropic).


### Требования 1/3 | Architecture & Design (40% веса оценки).


Вы создаете полный пакет проектной документации (Architecture Decision Records - ADR). Требуется представить 4 уровня детализации (C4 Model) + специализированные диаграммы:


- C4 Level 1 (Context): Интеграция AI-платформы в ландшафт (ERP, CRM, User Channels).

- C4 Level 2 (Container): Микросервисная архитектура (API Gateway, Vector DB, LLM Serving Engine, Orchestrator, Frontend).

- C4 Level 3 (Component): Внутреннее устройство Агента (Memory module, Planner, Tools interface).

- Deployment Diagram: Физическое размещение. Учет GPU-ресурсов, балансировка нагрузки, сегментация сети (DMZ, Internal), управление секретами (Vault).

- Sequence Diagram: Детальный флоу обработки сложного запроса (User -> Guardrails -> Rerank -> Agent Loop -> Tool Execution -> Response).

- ER Diagram: Модель данных. Хранение векторов, чанков, истории сессий, логов, прав доступа (RBAC).

### Требования 2/3 | Infrastructure & Stack (Актуальность: 2026, РФ).


Вы обязаны обосновать выбор стека в ADR (Trade-off analysis).


- LLM Serving: Высокопроизводительный сервер (vLLM, SGLang, TGI). Обязательно: квантование (AWQ/GGUF) для Consumer GPU, KV-cache optimization.

- Models: Open Source с поддержкой русского языка (Qwen 2.5/3, DeepSeek-V3, T-lite/Saiga).

- Vector Database: Self-hosted (Qdrant, Milvus, Weaviate).

- Orchestration: LangGraph (Stateful Agents) или LlamaIndex Workflows. Линейные цепочки запрещены.

- Observability: OpenTelemetry (Tracing запросов), Prometheus/Grafana (метрики токенов/сек, latency).

### Требования 3/3 | Implementation (MVP).


Реализация функционала согласно одной из тем:
- Cognitive Architecture: Реализация паттернов ReAct, Plan-and-Solve или Multi-Agent Collaboration.

- Advanced RAG: Использование GraphRAG (Knowledge Graphs) или Multimodal RAG (работа с изображениями/таблицами).

- Security: Реализация Input/Output Guardrails (фильтрация PII, защита от prompt injection).

### Рекомендации по выполнению:


- Не пытайтесь обучать модель! Ваша задача - архитектура и RAG. Используйте готовые Pre-trained модели (Qwen 3-30B-Instruct, DeepSeek-V3-Distill).

- Graph Schema: Начните с простой онтологии (3-4 типа узлов), не усложняйте схему базы данных сразу.

- Hardware: Если не хватает видеопамяти, используйте «Offloading» (выгрузку слоев на CPU) или арендуйте GPU-сервер в облаке (Yandex DataSphere/Cloud.ru) на пару часов для записи демо.

### Материалы для проекта:

#### Docker Hub образы локальных баз:
- Neo4j - https://hub.docker.com/_/neo4j
- Qdrant - https://hub.docker.com/r/qdrant/qdrant

#### Datasets:

Dataset 1 - https://github.com/irlcode/RusLawOD/blob/master/README.md
Dataset 2 - https://huggingface.co/datasets/irlspbru/RFSD

#### Code Templates:

Шаблон сервиса на FastAPI + LangGraph + vLLM Client - https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template

#### Формат сдачи:

1. Репозиторий (Monorepo):
   - /infra: Helm charts или docker-compose для поднятия всего стека (DBs + Apps).
   - /backend: Код агентов (Python) и API.
   - /docs: Архитектурная документация (ADD) в Markdown/PDF.

2. Видео-демо (Deep Dive):

   - 5-7 минут.
   - Показ работы «под капотом»: демонстрация трейсов в Jaeger/Langfuse, логов vLLM, визуализация построенного графа в браузере Neo4j/Falcor/Nebula.

3. Нагрузочный отчёт:

- Краткая справка: сколько RPS держит система на вашем железе, какая латентность.


### Критерии оценки:

Статус «Не принято», если:
- Используются облачные API (OpenAI/Anthropic).
- Отсутствует диаграмма Deployment или Data Flow.
- Нет реализации GraphRAG (простой векторный поиск не принимается в этом варианте).
Критично:

- Security: Проверка прав доступа работает (User B не получает ответ по секретному документу).
- Architecture: Разделение на Control Plane (Агенты) и Data Plane (БД/Модели).
- Stack: Использование LangGraph (или аналога State Machine), а не линейных скриптов.

Желательно:
- Реализация потокового ответа (Streaming) пользователю.
- Наличие Unit-тестов на промпты (LLM-as-a-Judge).