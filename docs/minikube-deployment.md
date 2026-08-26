# Развёртывание GraphRAG в Minikube (этап 10)

> K8s-ступень платформы: тот же код и образы, что в compose-стеке (этапы 2–9),
> упакованные в Helm chart `infra/helm/graphrag/`. Документ описывает устройство,
> воспроизведение с нуля, повседневный запуск и разбор реальных грабель.

## 1. Что где крутится

| Компонент | Образ | Примечание |
|---|---|---|
| backend | `vvsem/graphrag-backend:stage10` | FastAPI+LangGraph; bge-m3/reranker in-process (CPU) |
| frontend | `vvsem/graphrag-frontend:stage10` | nginx: SPA + прокси API-путей на backend:8000 |
| postgres | `postgres:16-alpine` | users/sessions/audit/chat |
| qdrant | `qdrant/qdrant:v1.19.0` | коллекция chunks, payload clearance |
| neo4j | `neo4j:5.26.30-community` | граф Act/Authority/Topic/Concept |
| vault | `hashicorp/vault:2.0.4` | dev-mode; секреты приложения в KV |
| Job vault-seed | тот же образ vault | кладёт APP_* секреты в KV при install/upgrade |
| Job corpus-init | `vvsem/graphrag-corpus:stage10` | копирует corpus_test на PVC |

Имена Service совпадают с именами сервисов compose (`backend`, `postgres`,
`neo4j`, ...) — поэтому nginx-конфиг фронтенда и env бэкенда не меняются.

## 2. Схема потоков

```text
Браузер / Newman
   |  http://127.0.0.1:8080   (kubectl port-forward -> ingress-nginx:80)
   v
[ingress-nginx]  пути /api,/auth,/admin,/health,/metrics,/docs,/openapi.json -> backend:8000
                 путь /                                                      -> frontend:80 (SPA)
   |
   v  внутри namespace graphrag
[backend] -- bolt://neo4j:7687 --> [neo4j]      (PVC neo4j-data)
         -- http://qdrant:6333 -> [qdrant]      (PVC qdrant-data)
         -- postgres:5432 -----> [postgres]     (PVC pg-data)
         -- host.minikube.internal:1234/v1 --> LM Studio на хосте Windows (LLM)
         -- host.minikube.internal:3300    --> Langfuse в Docker на хосте (трейсы промптов)
         -- host.minikube.internal:4318    --> otel-collector проекта
                                              graphrag-observability (трейсы -> Jaeger)

Наблюдаемость (Jaeger/Prometheus/Grafana) - ОТДЕЛЬНЫЙ compose-проект на хосте:
  cd infra && docker compose -f docker-compose.observability.yml up -d
  Jaeger http://127.0.0.1:16686 | Prometheus :9090 | Grafana :3000
  Prometheus job backend-k8s скрейпит бэкенд через ingress port-forward (:8080).

Секреты:
  secrets.yaml --(helm)--> K8s Secret graphrag-secrets
  Job vault-seed --vault kv put--> Vault KV secret/graphrag/app
  initContainer backend (app.infra.vault_fetch) --GET KV--> /config/secrets.env (tmpfs)
  команда backend: ". /config/secrets.env && alembic upgrade head && uvicorn"
```

Важно: IP ноды (192.168.49.x) из Windows НЕ маршрутизируется (docker-driver живёт
внутри WSL VM; kubeconfig ходит через localhost-прокси Docker Desktop). Поэтому
вход всегда через port-forward к ingress-контроллеру.

## 3. Бюджет ресурсов

| Под | requests | limits |
|---|---|---|
| backend | 2Gi / 500m | 4Gi / 4 CPU |
| neo4j | 512Mi / 100m | 2Gi / 2 CPU (+heap 1G, pagecache 512M env-капами) |
| qdrant | 512Mi / 100m | 1Gi / 2 CPU |
| postgres | 256Mi / 100m | 512Mi / 1 CPU |
| frontend+vault | ~96Mi | ~192Mi |
| Итого подов | | <= 8Gi из 12Gi VM |

Кластер: профиль `graphrag` (docker driver), капы задаются при старте:
`minikube -p graphrag start --memory=12288 --cpus=8`.
Перед стартом core compose-стек останавливается; Langfuse остаётся в Docker.
Контроль: `kubectl top nodes/pods` (metrics-server addon).

## 4. Воспроизведение с нуля

Предпосылки: Docker Desktop запущен; minikube/kubectl/helm установлены;
`docker login` в Docker Hub (аккаунт vvsem); LM Studio слушает :1234
(модель = `app.llmModel`, сейчас qwen3.5-2b); Langfuse-стек поднят (опционально).

```powershell
# 1) Кластер (если остановлен) - капы применятся при первом создании профиля
minikube -p graphrag start --driver=docker --memory=12288 --cpus=8 `
         --addons=ingress --addons=metrics-server

# 2) Сборка и публикация образов (из корня репо)
docker build -f backend/Dockerfile -t vvsem/graphrag-backend:stage10 backend
docker tag graphrag/frontend:stage8 vvsem/graphrag-frontend:stage10
docker build -f infra/corpus-loader/Dockerfile -t vvsem/graphrag-corpus:stage10 .
docker push vvsem/graphrag-backend:stage10
docker push vvsem/graphrag-frontend:stage10
docker push vvsem/graphrag-corpus:stage10

# 3) Прогрев ноды образами (kubelet скачал бы сам, но так быстрее/детерминированнее)
minikube -p graphrag ssh "sudo docker pull vvsem/graphrag-backend:stage10"
minikube -p graphrag ssh "sudo docker pull vvsem/graphrag-frontend:stage10"
minikube -p graphrag ssh "sudo docker pull vvsem/graphrag-corpus:stage10"
# БД/vault подтянутся kubelet'ом из Docker Hub при первом старте подов

# 4) Секреты: один раз скопировать шаблон и заполнить
Copy-Item infra/helm/graphrag/secrets.yaml.example infra/helm/graphrag/secrets.yaml

# 5) Установка релиза
helm install graphrag infra/helm/graphrag -n graphrag --create-namespace `
      -f infra/helm/graphrag/values.yaml -f infra/helm/graphrag/secrets.yaml

# 6) Дождаться зелёных подов (vault-seed и corpus-init должны стать Completed)
kubectl -n graphrag get pods,jobs

# 7) Входная точка: фоновый port-forward на ingress-контроллер
Start-Process kubectl -ArgumentList '-n','ingress-nginx','port-forward',
    'svc/ingress-nginx-controller','8080:80' -WindowStyle Hidden
curl.exe http://127.0.0.1:8080/health        # -> {"status":"ok"}

# 8) Сид пользователей (exec-сессия не видит vault-секретов - source вручную)
$pod = kubectl -n graphrag get pods -l app=backend -o jsonpath="{.items[0].metadata.name}"
'import asyncio; from app.config import Settings; from app.db.seed import DEFAULT_PASSWORDS, seed;
print(asyncio.run(seed(Settings(), DEFAULT_PASSWORDS)))' |
  kubectl -n graphrag exec -i $pod -c backend -- sh -c ". /config/secrets.env && python -"

# 9) Ingestion корпуса: админ -> POST /admin/ingest (10-30 мин, CPU)
#    Либо через UI Admin-страницу, либо curl:
$body = '{"username":"admin","password":"admin123"}' | Set-Content tmp_login.json -Encoding utf8
$tok = ((curl.exe -s -X POST http://127.0.0.1:8080/auth/login `
        -H "Content-Type: application/json" --data-binary "@tmp_login.json") |
        ConvertFrom-Json).access_token
curl.exe -s -X POST http://127.0.0.1:8080/admin/ingest -H "Authorization: Bearer $tok"

# 10) Newman gate против кластера
powershell scripts/run_postman.ps1 -BaseUrl http://127.0.0.1:8080
```

## 5. Повседневный запуск

Скрипты (см. `scripts/`):

| Скрипт | Что делает |
|---|---|
| `scripts/k8s_status.ps1` | поды/jobs, kubectl top, проверка `http://127.0.0.1:8080/health` |
| `scripts/k8s_up.ps1` | стартует остановленный кластер, `helm upgrade --install`, записывает секреты в Vault, поднимает port-forward, печатает статус |
| `scripts/k8s_down.ps1` | глушит port-forward/tunnel; `minikube stop` освобождает RAM (данные PVC сохраняются) |

Все скрипты принимают `-Profile <name>` (по умолчанию `graphrag`).

Makefile-цели:

```bash
make k8s-up              # полный запуск (профиль graphrag)
make k8s-up PROFILE=xxx  # другой профиль
make k8s-down            # остановить port-forward
make k8s-down StopCluster=1  # + minikube stop
make k8s-status          # статус подов
```

Ручной минимум:

```powershell
# утром
minikube -p graphrag start                   # если stop был
# ... helm upgrade + vault seed (скрипт делает автоматически) ...
Start-Process kubectl -ArgumentList '-n','ingress-nginx','port-forward',
     'svc/ingress-nginx-controller','8080:80' -WindowStyle Hidden

# вечером
Get-Process kubectl | Where-Object { $_.CommandLine -match 'port-forward' } | Stop-Process
minikube -p graphrag stop
```

UI для демо: `http://localhost:8080/` (SPA), Swagger: `http://localhost:8080/docs`.

### 5.1. Открыть приложение в браузере

IP ноды minikube из Windows не маршрутизируется (docker-driver живёт внутри WSL),
поэтому есть два способа:

**Способ А — port-forward (дефолт, без прав администратора):**

```powershell
Start-Process kubectl -ArgumentList '-n','ingress-nginx','port-forward',
    'svc/ingress-nginx-controller','8080:80' -WindowStyle Hidden
# браузер:
#   http://localhost:8080        - SPA (логин admin/admin123)
#   http://localhost:8080/docs   - Swagger UI
```

`make k8s-up` поднимает этот port-forward автоматически. Работает через тот же
ingress (path-routing и SSE-аннотации активны), просто на другом порту.

**Способ Б — домены через `minikube tunnel` (красивые URL, UAC один раз):**

```powershell
# 1) Включить именованные хосты ingress
helm upgrade graphrag infra/helm/graphrag -n graphrag `
     -f infra/helm/graphrag/values.yaml -f infra/helm/graphrag/secrets.yaml `
     --set ingress.hosts.enabled=true

# 2) Разовая запись в hosts (PowerShell ЗАПУЩЕН ОТ АДМИНИСТРАТОРА)
$ip = kubectl get nodes -o custom-columns=":.status.addresses[0].address"
Add-Content "$env:SystemRoot\System32\drivers\etc\hosts" "$ip graphrag.local api.graphrag.local"

# 3) Туннель (держать запущенным; при старте один раз спросит UAC -
#    добавляет маршрут Windows до сервисной сети кластера)
minikube -p graphrag tunnel

# 4) Браузер:
#    http://graphrag.local      - SPA
#    http://api.graphrag.local/docs - Swagger
```

Как это работает: tunnel делает service-CIDR кластера маршрутизируемым с хоста
и отдаёт ingress-контроллеру адрес 127.0.0.1:80; домены резолвит запись в hosts.
Останов туннеля (Ctrl+C) возвращает всё к способу А; флаг
`--set ingress.hosts.enabled=false` — к catch-all-режиму.
Способы А и Б можно совмещать (port-forward на 8080 живёт независимо).

## 6. Данные и полный teardown

Персистентность: PVC pg-data / qdrant-data / neo4j-data / hf-cache / corpus-pvc
переживают рестарты подов и `minikube stop/start`. Полный снос:

```powershell
helm uninstall graphrag -n graphrag
kubectl delete ns graphrag            # удалит и PVC (данные БД пропадут!)
minikube -p graphrag delete           # опционально: снести весь кластер
```

Возврат к compose-стеку: `cd infra && docker compose up -d`
(и `--profile observability`; Langfuse: `docker compose -f docker-compose.langfuse.yml up -d`).

## 7. Troubleshooting (реальные грабли этой сессии)

| Симптом | Причина | Лечение |
|---|---|---|
| `curl http://192.168.49.2` -> HTTP 000, kubectl при этом работает | IP ноды не маршрутизируется из Windows (kubeconfig идёт через `127.0.0.1:<port>` Docker Desktop) | вход только через port-forward к ingress-контроллеру (см. п.4 шаг 7) |
| PVC вечно Pending, event "no storage class is set" | в шаблоне было `storageClassName: ""` — это ЯВНОЕ отключение динамического провижининга | убрать поле совсем → default StorageClass `standard`; переустановить релиз (PVC пустые) |
| neo4j Error: `Unrecognized setting ... PORT.7687.TCP.PORT` | k8s инжектит service-link env по имени сервиса (`NEO4J_PORT_7687_TCP_PORT`), entrypoint Neo4j читает как настройки | env `NEO4J_server_config_strict__validation_enabled=false` (уже в chart) |
| backend падает: `jwt_secret Field required` | sourced-env без `export` не наследуется дочерними процессами (alembic/uvicorn) | vault_fetch пишет строки `export KEY='value'` (уже исправлено) |
| helm install падает на Ingress: webhook `...admission.ng.svc` connection refused | старый ingress-nginx (namespace ng, scale 0) оставил ValidatingWebhookConfiguration, которая блокирует ВСЕ Ingress кластера | `kubectl delete validatingwebhookconfiguration nginx-ingress-nginx-admission`; вернуть: `helm upgrade <release> -n ng` |
| `minikube image load <img>` exit 80: "unable to calculate manifest" | баг кэширования minikube на отдельных образах | тянуть изнутри ноды: `minikube ssh "sudo docker pull <img>"`, или пуш в Docker Hub + pull |
| C: забился при image load, всё висит | minikube копит образы в `%USERPROFILE%\.minikube\cache\images` ДО отправки в ноду; для образа 8+GB нужно столько же свободного места | чистить кэш после загрузок: `Remove-Item ~\.minikube\cache\images -Recurse -Force`; тяжёлые образы гнать через Hub (docker push/pull) |
| exec-сессия в backend не видит APP_JWT_SECRET | секреты из Vault попадают в env только PID 1 (через `. secrets.env` в команде пода) | в exec добавлять `sh -c ". /config/secrets.env && python -"` |
| Поды старых тестовых стендов жрут память кластера | профиль minikube общий с учебными стендами (efk/kafka/prometheus/cnpg/ng/portainer) | они приостановлены: deploy/sts `--replicas=0`, daemonset'ам добавлен nodeSelector `paused.nonexistent=true`. Вернуть: снять nodeSelector патчем и scale обратно (напр. `kubectl patch ds metricbeat-metricbeat -n efk --type merge -p '{"spec":{"template":{"spec":{"nodeSelector":null}}}}'`) |
| Первый чат/ingestion «висят» минуты | bge-m3+reranker (~4.5GB) качаются с HF при первом использовании | норма; кэш живёт в PVC hf-cache, повторных скачиваний нет |
| obs-порты 3000/9090 заняты при старте `graphrag-observability` | старый langfuse-проект (v2) поднялся после рестарта Docker из-за `restart: unless-stopped` и держит minio:9090/web:3000 | `docker compose -p langfuse stop` (данные в томах); правило «два langfuse не держать» |

## 8. Проверка ресурсов

```powershell
kubectl top nodes
kubectl top pods -n graphrag --sort-by=memory
docker stats --no-stream minikube        # потребление VM целиком
```

Ориентир (после прогона ingestion): нода ~3.3GiB / 12GiB капа VM,
backend ~2-4GiB после прогрева моделей, neo4j ~1GiB.
