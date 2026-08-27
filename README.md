# 2026-otus-proj

GraphRAG-платформа корпоративных знаний (OTUS, 2026): закрытый контур,
LangGraph-агент, гибридный поиск (Qdrant + Neo4j), RBAC, наблюдаемость.

## Режимы запуска

| Режим | Команда | Документация |
|---|---|---|
| **Compose** (разработка) | `docker compose -f infra/docker-compose.yml up -d` | `infra/README.md` |
| **Minikube/Helm** (целевой, этап 10) | `make k8s-up` → http://localhost:8080 | `docs/minikube-deployment.md` |

Тестирование (пирамида, последовательности, гигиена данных): [tests/README.md](tests/README.md).
Коротко: `make lint` → `make test-unit` → `make test-integration` → `make test-postman`
(для последнего нужен поднятый стек и LM Studio).

Быстрый старт в minikube (предпосылки и полный runbook — в
[docs/minikube-deployment.md](docs/minikube-deployment.md)):

```powershell
make k8s-up          # кластер + helm upgrade + port-forward + статус
# UI: http://localhost:8080   Swagger: http://localhost:8080/docs
make test-postman POSTMAN_ARGS="-BaseUrl http://127.0.0.1:8080"   # E2E gate против кластера
make k8s-down        # остановить port-forward (+ -StopCluster для minikube stop)
```

Наблюдаемость — самостоятельный проект на хосте (переиспользуемый обоими режимами):
`docker compose -f infra/docker-compose.observability.yml up -d`
→ Jaeger :16686, Prometheus :9090, Grafana :3000.

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
