# Инфраструктура GraphRAG Platform (этап 2)

Docker Compose стек платформы. Всё поднимается одной командой, все настройки — через `.env`.

## Состав стека

### Базовый стек (`docker-compose.yml`)

| Сервис | Образ | Зачем | Доступ |
|---|---|---|---|
| traefik | traefik:v3.7.11 | API Gateway / reverse proxy, роутинг по поддоменам | http://traefik.localhost/dashboard/ ; порт `APP_TRAEFIK_HTTP_PORT` (80) |
| backend | graphrag/backend:stage2 (сборка из `../backend`) | Заглушка FastAPI: `/health`, `/metrics` | http://api.localhost/health ; 127.0.0.1:`APP_BACKEND_PORT` (8000) |
| postgres | postgres:16-alpine | Пользователи/роли/сессии/аудит (этап 3+) | 127.0.0.1:`APP_POSTGRES_PORT` (5432), БД `graphrag` |
| qdrant | qdrant:v1.19.0 | Векторная БД (чанки + эмбеддинги, этап 4+) | 127.0.0.1:`APP_QDRANT_HTTP_PORT` (6333), дашборд `/dashboard`; gRPC 6334 |
| neo4j | neo4j:5.26.30-community | Граф знаний Act/Authority/Topic/Concept (этап 4+) | Browser: http://neo4j.localhost , логин `neo4j` / `APP_NEO4J_PASSWORD`; bolt 7687 |
| vault | hashicorp/vault:2.0.4 (dev-mode) | Хранение секретов (по заданию) | 127.0.0.1:`APP_VAULT_PORT` (8200), токен `APP_VAULT_DEV_ROOT_TOKEN` |

### Наблюдаемость — самостоятельный проект `docker-compose.observability.yml` (этап 10)

Отдельный compose-проект `graphrag-observability` (по аналогии с `graphrag-langfuse`):
живёт независимо от основного стека, потребители (compose-бэкенды, minikube-бэкенд,
хост-скрипты) подключаются через host-порты на 127.0.0.1. Traefik основного стека
НЕ требуется — UI открываются при остановленном всём остальном.

| Сервис | Образ | Зачем | Доступ |
|---|---|---|---|
| otel-collector | otel/opentelemetry-collector-contrib:0.159.0 | Приём OTLP-трейсов → Jaeger | `127.0.0.1:4318` (HTTP) / `4317` (gRPC); из контейнеров — `host.docker.internal`, из подов minikube — `host.minikube.internal` |
| jaeger | jaegertracing/all-in-one:1.74.0 | Трейсы запросов | http://127.0.0.1:16686 |
| prometheus | prom/prometheus:v3.7.3 | Метрики; job'ы `backend` (compose :8000) и `backend-k8s` (:8080 через ingress PF) | http://127.0.0.1:9090 |
| grafana | grafana/grafana:12.3.11 | Дашборды; Prometheus+Jaeger провиженятся автоматически | http://127.0.0.1:3000 , `APP_GRAFANA_ADMIN_USER/PASSWORD` |

Запуск/остановка:

```powershell
cd infra
docker compose -f docker-compose.observability.yml up -d      # старт
docker compose -f docker-compose.observability.yml stop       # стоп (данные томов целы)
```

### LLM (опционально)

- **Dev-режим (по умолчанию):** LM Studio на хосте, OpenAI-compatible API.
  В `.env`: `APP_LLM_BASE_URL=http://host.docker.internal:1234/v1`, `APP_LLM_MODEL=qwen/qwen3.5-9b`.
  Из контейнеров хост доступен только как `host.docker.internal`.
- **Целевой vLLM** (`-f docker-compose.gpu.yml`, образ ~11 ГБ): `vllm/vllm-openai:v0.27.1-cu129`
  на RTX 5070 Ti (sm_120 → CUDA ≥12.8). Для демо/нагрузочного отчёта; переключение — одна строка
  `APP_LLM_BASE_URL=http://vllm:8000/v1`. См. ADR-001 «Дополнение».

### Сети (сегментация по Deployment-диаграмме)

- `graphrag_edge` — Control Plane сторона: traefik ↔ backend ↔ UI наблюдаемости;
- `graphrag_data` — Data Plane: backend ↔ postgres/qdrant/neo4j/vault/vllm/otel-collector.

Порты БД публикуются **только на 127.0.0.1**, наружу (LAN) не светятся.

---

## Быстрый старт

Из корня репозитория (Windows PowerShell):

```powershell
# 1. Один раз: создать .env с дев-паролями
Copy-Item infra\.env.example infra\.env

# 2. Поднять базовый стек
docker compose -f infra/docker-compose.yml up -d

# 2a. + наблюдаемость (отдельный проект graphrag-observability)
docker compose -f infra/docker-compose.observability.yml up -d

# 3. Проверить, что всё зелёное
python scripts\smoke_infra.py --with-obs
```

Smoke печатает таблицу OK/FAIL/SKIP и возвращает ненулевой exit code при проблемах.
LLM-строка проверяет LM Studio (`/v1/models`) — она зелёная, когда LM Studio запущен с загруженной моделью.

## Повседневные команды

```powershell
# статус и здоровье контейнеров
docker compose -f infra/docker-compose.yml ps

# логи сервиса
docker compose -f infra/docker-compose.yml logs -f neo4j

# остановить всё (данные сохраняются в docker volumes)
docker compose -f infra/docker-compose.yml down
docker compose -f infra/docker-compose.observability.yml stop

# полный сброс ВМЕСТЕ с данными (граф, векторы, метрики)
docker compose -f infra/docker-compose.yml down -v
docker compose -f infra/docker-compose.observability.yml down -v

# пересобрать бэкенд после правок кода
docker compose -f infra/docker-compose.yml up -d --build backend
```

Имена volume'ов стабильны (`graphrag_pg_data`, `graphrag_qdrant_data`,
`graphrag_neo4j_data`, …) — данные переживают `down` и перезапуски.

## Корпус: как пополнять (задача «Корпус»)

Каталог корпуса монтируется в backend как живой bind mount — новые XML подхватываются
без пересборки образов и перезапуска:

```yaml
# docker-compose.yml, сервис backend
- ${CORPUS_HOST_DIR:-../corpus_test}:/data/corpus:rw
```

Способы пополнения (любой → затем «Запустить ingestion» в Admin UI или
`POST /admin/ingest`; прогон инкрементальный — обработается только дельта по sha256):

1. **Папка на хосте**: скопировать XML в `CORPUS_HOST_DIR` (по умолчанию
   `corpus_test/` в корне репозитория). Свой каталог — одна строка в `infra/.env`:
   `CORPUS_HOST_DIR=D:/data/my_corpus` (абсолютный путь тоже работает);
2. **Через API/UI** (`POST /admin/documents`, admin-only, multipart): только `.xml`,
   лимит `APP_INGEST_MAX_UPLOAD_MB` на файл; загрузка только сохраняет файлы —
   прогон запускается отдельно. `DELETE /admin/documents/{filename}` — удалить файл;
3. **`docker cp`**: `docker cp act.xml graphrag-backend:/data/corpus/` — если папка
   хоста недоступна.

Маунт rw нужен именно для способа 2. Менять путь корпуса — только через
`CORPUS_HOST_DIR` (код не трогается). После смены процентов грифа
(`APP_INGEST_*_PERCENT`) — полный прогон `POST /admin/ingest?full=true`.

## Подъём руками, по шагам (если что-то пошло не так)

1. Убедиться, что Docker Desktop запущен и движок живой: `docker info`.
2. Создать `.env` из шаблона (см. Быстрый старт), при необходимости поменять пароли/порты.
3. `docker compose -f infra/docker-compose.yml up -d` — ядро (traefik, backend, postgres,
   qdrant, neo4j, vault). Neo4j первый раз стартует ~1–1.5 мин (healthcheck ждёт до 90 c).
4. Добавить наблюдаемость: `docker compose -f infra/docker-compose.observability.yml up -d`
   (отдельный проект, порты на 127.0.0.1: jaeger 16686 / prometheus 9090 / grafana 3000).
5. Проверка: `python scripts\smoke_infra.py --with-obs` — все строки OK.
6. Открыть в браузере: http://127.0.0.1:3000 (Grafana), http://127.0.0.1:16686 (Jaeger),
   http://neo4j.localhost (Neo4j Browser, через traefik основного стека).
   Grafana спросит логин из `.env`; источники данных уже подключены.
7. Для инференса — запустить LM Studio, включить сервер (порт 1234), загрузить модель
   (например `qwen/qwen3.5-9b`). Проверка: строка `llm ...` в smoke или
   `curl.exe --noproxy "*" http://127.0.0.1:1234/v1/models`.

## Типовые проблемы

| Симптом | Причина / лечение |
|---|---|
| LM Studio: «Failed to load model», в логах `cudaMalloc failed: device busy or unavailable` | Залипший CUDA-контекст WSL после жёстко убитого GPU-контейнера. Лечится: закрыть Docker Desktop → `wsl --shutdown` → запустить Docker Desktop заново → поднять стек командами выше. |
| Порт 80/5432/… занят другим приложением | Поменять соответствующий `APP_*_PORT` в `.env`, `docker compose up -d` пересоздаст сервисы. |
| Браузер не открывает `*.localhost` | Редкий случай прокси/старого DNS: добавить в `C:\Windows\System32\drivers\etc\hosts` строки `127.0.0.1 <имя>.localhost`, либо обращаться через прямые порты. |
| curl к локальным сервисам падает | Корпоративный/системный прокси: добавлять `--noproxy "*"`. Из PowerShell слать JSON с кириллицей строго UTF-8 байтами. |
| `backend -> down` в Prometheus Targets | Нормально для заглушки до появления `/metrics` реального формата (этапы 3/7); сейчас заглушка отдаёт минимальный `/metrics`, цель должна быть `up`. |
| vllm target down в Prometheus | Ожидаемо: vLLM опционален и по умолчанию не запущен. |

## Что куда смотрит (порядок запуска зависимостей)

```
браузер ──► traefik (:80) ──► backend (api.localhost)
                        ├──► grafana / jaeger / prometheus / neo4j browser (поддомены)
backend ──► postgres / qdrant / neo4j / vault          (сеть data)
backend ──► host.docker.internal:1234 (LM Studio)      (dev-LLM)
backend ──► otel-collector:4317 ──► jaeger             (трейсы, этап 7)
prometheus ──► backend:8000/metrics, vllm:8000/metrics (метрики)
```

Все версии образов зафиксированы в compose-файлах; обновления — осознанной правкой пина.
