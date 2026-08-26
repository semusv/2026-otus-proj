# 2026-otus-proj

GraphRAG-платформа корпоративных знаний (OTUS, 2026): закрытый контур,
LangGraph-агент, гибридный поиск (Qdrant + Neo4j), RBAC, наблюдаемость.

## Режимы запуска

| Режим | Команда | Документация |
|---|---|---|
| **Compose** (разработка) | `docker compose -f infra/docker-compose.yml up -d` | `infra/README.md` |
| **Minikube/Helm** (целевой, этап 10) | `make k8s-up` → http://localhost:8080 | `docs/minikube-deployment.md` |

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
