# GraphRAG Platform — общий Makefile (этап 3+)
# Требования: uv (https://docs.astral.sh/uv/), GNU Make.
# Все python-команды выполняются из backend/ (uv --project), чтобы подхватить .venv и pyproject.

BACKEND_DIR := backend
FRONTEND_DIR := frontend

.PHONY: help lint fmt format check sync lock test-unit test-integration test-all test-judge seed-users openapi-export pre-commit-install frontend-install frontend-lint frontend-test frontend-build openapi-types postman-run test-postman load-test bench-llm k8s-up k8s-down k8s-status

help:
	@echo "lint              - ruff check + mypy (gate всех этапов)"
	@echo "fmt               - ruff format + autofix"
	@echo "sync              - uv sync dev-окружения backend"
	@echo "lock              - обновить uv.lock после правки pyproject.toml"
	@echo "test-unit         - быстрые тесты (без compose)"
	@echo "test-integration  - интеграционные тесты (нужен docker compose up postgres)"
	@echo "test-all          - unit + integration"
	@echo "test-judge        - LLM-as-a-Judge промпт-тесты (нужен запущенный LM Studio)"
	@echo "seed-users        - создать роли и 3 пользователей (viewer/analyst/admin) в PG"
	@echo "openapi-export    - экспортировать OpenAPI-контракт в docs/api/openapi.yaml"
	@echo "frontend-install  - npm ci для frontend"
	@echo "frontend-lint     - eslint + tsc --noEmit для frontend"
	@echo "frontend-test     - vitest smoke для frontend"
	@echo "frontend-build    - production build SPA"
	@echo "openapi-types     - регенерация типов фронта из docs/api/openapi.yaml"
	@echo "test-postman      - E2E newman против работающего стека (gate, exit-code)"
	@echo "postman-run       - алиас test-postman"
	@echo "load-test         - locust-нагрузочный прогон (профили health|chat: LOAD_PROFILE=...)"
	@echo "bench-llm         - micro-bench tokens/sec движка LLM (LM Studio/vLLM)"
	@echo "k8s-up            - запуск стека в minikube: кластер + helm upgrade + port-forward"
	@echo "k8s-down          - остановка port-forward (+ -StopCluster для minikube stop)"

lint:
	cd $(BACKEND_DIR) && uv run ruff check .
	cd $(BACKEND_DIR) && uv run mypy app

fmt:
	cd $(BACKEND_DIR) && uv run ruff check --fix .
	cd $(BACKEND_DIR) && uv run ruff format .

format: fmt

check: lint

sync:
	cd $(BACKEND_DIR) && uv sync

lock:
	cd $(BACKEND_DIR) && uv lock

pre-commit-install:
	pre-commit install

test-unit:
	cd $(BACKEND_DIR) && uv run pytest -m unit

test-integration:
	cd $(BACKEND_DIR) && uv run pytest -m integration --timeout=300

test-all: test-unit test-integration

test-judge:
	cd $(BACKEND_DIR) && uv run pytest -m gpu_slow

seed-users:
	cd $(BACKEND_DIR) && uv run python ../scripts/seed_users.py

openapi-export:
	cd $(BACKEND_DIR) && uv run python ../scripts/export_openapi.py

frontend-install:
	cd $(FRONTEND_DIR) && npm ci

frontend-lint:
	cd $(FRONTEND_DIR) && npm run lint
	cd $(FRONTEND_DIR) && npm run typecheck

frontend-test:
	cd $(FRONTEND_DIR) && npm test

frontend-build:
	cd $(FRONTEND_DIR) && npm run build

openapi-types:
	cd $(FRONTEND_DIR) && npm run gen:api

postman-run: test-postman

test-postman:
	powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_postman.ps1

load-test:
	powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_load_test.ps1

bench-llm:
	cd $(BACKEND_DIR) && uv run python ../scripts/load_test/bench_llm.py

# --- Этап 10: minikube ---
k8s-up:
	powershell -NoProfile -ExecutionPolicy Bypass -File scripts/k8s_up.ps1

k8s-down:
	powershell -NoProfile -ExecutionPolicy Bypass -File scripts/k8s_down.ps1

k8s-status:
	powershell -NoProfile -ExecutionPolicy Bypass -File scripts/k8s_status.ps1
