# ADR-010: Стратегия развёртывания

- Статус: принято
- Дата: 2026-08-25
- Связанные: ADR-001 (GPU-образ), этапы 2 и 10

## Контекст

Задание требует манифестов k8s/minikube как целевого состояния, но итерации разработки
(этапы 2–9) выигрывают от простого локального окружения. Windows + Docker Desktop (WSL2)
с GPU passthrough уже настроены.

## Решение

**Двухступенчато:** разработка и интеграционная проверка этапов 2–9 — **docker-compose**
(`infra/docker-compose.yml` + override'ы `gpu`, `observability`); целевая сборка —
**minikube + Helm** (`infra/helm/`) последним этапом 10, с теми же образами и env-конфигурацией.

## Trade-offs

| Критерий | Сразу k8s/minikube | Только compose | Compose → minikube (наш выбор) |
|----------|--------------------|----------------|-------------------------------|
| Скорость итераций этапов 2–9 | Низкая (rebuild/redeploy циклы) | Высокая | Высокая |
| GPU passthrough в WSL2 | Капризнее дебажить | Просто | Просто на этапе разработки |
| Соответствие заданию (манифесты) | Сразу | Нет — отклонено | Есть к финалу |
| Риск «работает в compose, не работает в k8s» | Ниже | Не применим | Средний, гасится едиными env |

## Последствия

- Положительные: инфраструктура поднимается одной командой с этапа 2; конфиги сервисов
  с первого дня только через env → перенос в Helm почти механический; Vault/observability
  одинаковы в обеих ступенях.
- Отрицательные / риски:
  - двойная поддержка описаний деплоя (compose + helm charts) в конце проекта;
  - GPU scheduling в minikube потребует отдельной проверки драйвера — заложено в этап 10.

## Дополнение (этап 10, minikube)

При реализации k8s-ступени приняты отклонения от compose-схемы (MVP, один узел):

| Вопрос | Compose (этапы 2–9) | Minikube (этап 10) | Обоснование |
|--------|--------------------|--------------------|-------------|
| API Gateway / Ingress | Traefik (labels) | **ingress-nginx addon** + catch-all Ingress по IP ноды | Traefik-in-cluster тянет CRD/RBAC и дублирует шлюз; nginx addon — одна команда; hosts-файл не трогаем (нет прав админа), NodePort — fallback |
| Секреты | Vault dev-mode стоит отдельно, backend читает env из compose | **Vault подключён к приложению**: seed-Job кладёт KV `secret/graphrag/app`, backend initContainer рендерит `/config/secrets.env` (модуль `app/infra/vault_fetch.py`) | Требование задания «управление секретами (Vault)» выполняется фактически; флаг `vault.enabled=false` откатывает на K8s Secret. Прод-эволюция: AppRole/K8s-auth вместо dev-root-token |
| LLM Serving | LM Studio хоста через host.docker.internal | **LM Studio хоста через host.minikube.internal** (vLLM/GPU в кластер не тащим) | GPU passthrough в minikube docker-driver — отдельный большой риск вне MVP-объёма; контракт APP_LLM_* одинаков |
| Langfuse | compose-стек graphrag-langfuse | **остаётся в Docker**, backend ходит через host.minikube.internal:3300 | Экономия RAM кластера (~2.4GB); отказоустойчивость к недоступности уже в коде (этап 7) |
| Jaeger/Prom/Grafana | observability-профиль compose | **в кластер не дублируются** (`APP_TRACING_ENABLED=false` дефолт) | Демо наблюдаемости остаётся за compose; `/metrics` работает всегда |
| Корпус | bind mount ../corpus_test | init-Job копирует corpus (3.2MB) на PVC | Пересборка образа не нужна |

Ресурсная рамка (хост 64GB): minikube VM капится `--memory=12288 --cpus=6`; поды ≤8Gi
(backend 4Gi из-за CPU-эмбеддера bge-m3+reranker, neo4j 2Gi c heap/pagecache-капами,
qdrant 1Gi, pg 512Mi, frontend+ingress+vault <0.5Gi). Перед стартом кластера core
compose-стек останавливается.
