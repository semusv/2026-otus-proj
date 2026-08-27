"""Smoke-проверка инфраструктуры этапа 2 (stdlib only).

Проверяет:
  1. Статусы контейнеров из `docker compose ps` (healthy/running).
  2. HTTP-эндпоинты напрямую на опубликованных портах 127.0.0.1.
  3. Роутинг через Traefik по поддоменам *.localhost (подмена заголовка Host).
  4. Внешний Langfuse, если LANGFUSE_URL задан в .env.

Использование:
  python scripts/smoke_infra.py                 # базовый стек
  python scripts/smoke_infra.py --with-obs      # + jaeger/prometheus/grafana/otel
  python scripts/smoke_infra.py --with-gpu      # + vllm
  python scripts/smoke_infra.py --with-langfuse # + langfuse (отдельный проект)
Exit code: 0 - всё зелёное, 1 - есть FAIL.
"""

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_DIR = REPO_ROOT / "infra"

BASE_SERVICES = ["traefik", "backend", "postgres", "qdrant", "neo4j", "vault"]
OBS_SERVICES = ["otel-collector", "jaeger", "prometheus", "grafana"]
GPU_SERVICES = ["vllm"]
LANGFUSE_SERVICES = [
    "langfuse-web",
    "langfuse-worker",
    "langfuse-postgres",
    "langfuse-redis",
    "langfuse-clickhouse",
    "langfuse-minio",
]
# Одноразовая джоба не проверяется на running
LANGFUSE_INIT_SERVICE = "langfuse-bucket-init"

SERVICES_WITHOUT_HCHECK = {"jaeger", "grafana", "otel-collector", "langfuse-web", "langfuse-worker"}

DIRECT_HTTP_CHECKS = [
    ("backend", "/health"),
    ("qdrant", "/healthz"),
]

TRAEFIK_HTTP_CHECKS = [
    ("api.localhost", "/health", "backend"),
    ("neo4j.localhost", "/", "neo4j"),
]

# Наблюдаемость - самостоятельный проект (graphrag-observability): прямые порты
# на 127.0.0.1, traefik основного стека не участвует
OBS_HTTP_CHECKS = [
    # (сервис, env-переменная порта, дефолт, путь)
    ("jaeger", "APP_JAEGER_UI_PORT", "16686", "/"),
    ("prometheus", "APP_PROMETHEUS_PORT", "9090", "-/healthy"),
    ("grafana", "APP_GRAFANA_PORT", "3000", "/api/health"),
]

# Langfuse - самостоятельный проект (graphrag-langfuse): прямой порт 3300,
# а также через Traefik если он запущен
LANGFUSE_DIRECT_CHECK = ("langfuse-web", "APP_LANGFUSE_PORT", "3300", "/api/public/health")
LANGFUSE_TRAEFIK_CHECK = ("langfuse.localhost", "/api/public/health", "langfuse-web")


def load_env(path: Path) -> dict[str, str]:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def get_all_containers(args: argparse.Namespace) -> dict[str, dict]:
    """Собирает состояние контейнеров из всех нужных compose-проектов."""
    projects = []

    # Основной проект graphrag
    main_files = [str(COMPOSE_DIR / "docker-compose.yml")]
    if args.with_obs:
        main_files.append(str(COMPOSE_DIR / "docker-compose.observability.yml"))
    if args.with_gpu:
        main_files.append(str(COMPOSE_DIR / "docker-compose.gpu.yml"))
    if args.with_langfuse:
        main_files.append(str(COMPOSE_DIR / "docker-compose.langfuse.yml"))
    projects.append(("graphrag", main_files))

    # Отдельный проект observability (если включён)
    if args.with_obs:
        obs_files = [str(COMPOSE_DIR / "docker-compose.observability.yml")]
        projects.append(("graphrag-observability", obs_files))

    # Отдельный проект langfuse (если включён)
    if args.with_langfuse:
        lf_files = [str(COMPOSE_DIR / "docker-compose.langfuse.yml")]
        projects.append(("graphrag-langfuse", lf_files))

    all_services = {}
    for project, files in projects:
        cmd = ["docker", "compose", "-p", project, "--project-directory", str(COMPOSE_DIR)]
        for f in files:
            cmd += ["-f", f]
        cmd += ["ps", "--format", "json"]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            print(f"WARN: не удалось получить ps для проекта {project}: {result.stderr}")
            continue

        out = result.stdout.strip()
        if not out:
            continue

        try:
            data = json.loads(out)
            items = data if isinstance(data, list) else [data]
        except json.JSONDecodeError:
            # Если вывод представляет собой несколько JSON-объектов по одному на строку
            items = []
            for line in out.splitlines():
                if line.strip():
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

        for item in items:
            name = item.get("Service") or item.get("Name") or ""
            if name:
                all_services[name] = item

    return all_services


def check_containers(expected: list[str], running: dict[str, dict],
                     init_containers: list[str] | None = None) -> tuple[list, int]:
    rows, fails = [], 0
    init_containers = init_containers or []
    for svc in expected:
        info = running.get(svc)
        if info is None:
            rows.append((svc, "container", "FAIL", "контейнер не запущен"))
            fails += 1
            continue
        state = (info.get("State") or "").lower()
        health = info.get("Health")
        if isinstance(health, dict):
            health = health.get("Status")
        if svc in SERVICES_WITHOUT_HCHECK:
            ok, status = state == "running", f"running ({state})"
        else:
            ok, status = (state == "running" and health == "healthy"), f"{state}/{health}"
        if not ok:
            fails += 1
        rows.append((svc, "container", "OK" if ok else "FAIL", status))

    # Одноразовые джобы: проверяем, что они завершились успешно
    for svc in init_containers:
        info = running.get(svc)
        if info is None:
            rows.append((svc, "container", "FAIL", "контейнер не запущен"))
            fails += 1
            continue
        state = (info.get("State") or "").lower()
        exit_code = info.get("ExitCode", -1)
        ok = state == "exited" and exit_code == 0
        rows.append((svc, "container", "OK" if ok else "FAIL", f"{state} exit={exit_code}"))
        if not ok:
            fails += 1

    extra = set(running) - set(expected) - set(init_containers)
    if extra:
        rows.append((",".join(sorted(extra)), "container", "WARN",
                     "запущены, но не входят в выбранный набор"))
    return rows, fails


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_check(url: str, host_header: str | None = None) -> tuple[bool, str]:
    headers = {"Host": host_header} if host_header else {}
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with _OPENER.open(req, timeout=10) as resp:
            return resp.status == 200, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, type(e).__name__


def check_http(rows: list, kind: str, port: int, path: str,
               host: str = "127.0.0.1", host_header: str | None = None,
               label: str = "") -> int:
    if not path.startswith("/"):
        path = "/" + path
    name = label or (host_header or f"{host}:{port}")
    ok, detail = http_check(f"http://{host}:{port}{path}", host_header)
    rows.append((name, kind, "OK" if ok else "FAIL", detail))
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Инфра smoke-тест этапа 2")
    parser.add_argument("--with-obs", action="store_true",
                        help="ждать также otel/jaeger/prometheus/grafana")
    parser.add_argument("--with-gpu", action="store_true",
                        help="ждать также vllm")
    parser.add_argument("--with-langfuse", action="store_true",
                        help="ждать также langfuse (отдельный проект)")
    args = parser.parse_args()

    expected = list(BASE_SERVICES)
    init_containers = []
    if args.with_obs:
        expected += OBS_SERVICES
    if args.with_gpu:
        expected += GPU_SERVICES
    if args.with_langfuse:
        expected += LANGFUSE_SERVICES
        init_containers.append(LANGFUSE_INIT_SERVICE)

    running = get_all_containers(args)

    rows, fails = check_containers(expected, running, init_containers)

    env = load_env(COMPOSE_DIR / ".env")
    traefik_port = env.get("APP_TRAEFIK_HTTP_PORT", "80")

    # прямые проверки берём порты по умолчанию из .env
    direct_ports = {
        "backend": int(env.get("APP_BACKEND_PORT", "8000")),
        "qdrant": int(env.get("APP_QDRANT_HTTP_PORT", "6333")),
    }
    for svc, path in DIRECT_HTTP_CHECKS:
        if svc in expected:
            fails += check_http(rows, "direct", direct_ports[svc], path, label=f"{svc}{path}")

    for host, path, svc in TRAEFIK_HTTP_CHECKS:
        if svc not in expected:
            rows.append((host, "traefik", "SKIP", "сервис не в наборе"))
            continue
        fails += check_http(rows, "traefik", int(traefik_port), path,
                            host_header=host, label=host)

    for svc, port_env, port_default, path in OBS_HTTP_CHECKS:
        if svc not in expected:
            rows.append((svc, "obs-direct", "SKIP", "сервис не в наборе"))
            continue
        fails += check_http(rows, "obs-direct", int(env.get(port_env, port_default)),
                            path, label=f"{svc}:{path}")

    # Langfuse: прямой порт + через Traefik (если Traefik запущен)
    if args.with_langfuse:
        svc, port_env, port_default, path = LANGFUSE_DIRECT_CHECK
        port = int(env.get(port_env, port_default))
        fails += check_http(rows, "langfuse-direct", port, path, label="langfuse:3300")

        # Через Traefik проверяем только если traefik есть в expected
        if "traefik" in expected:
            host, path, svc = LANGFUSE_TRAEFIK_CHECK
            fails += check_http(rows, "langfuse-traefik", int(traefik_port), path,
                                host_header=host, label=host)
        else:
            rows.append(("langfuse.localhost", "langfuse-traefik", "SKIP", "Traefik не запущен"))

    # Внешний Langfuse (если задан LANGFUSE_URL в .env) – проверка на случай,
    # если используется внешний инстанс, а не локальный compose-проект.
    langfuse_url = env.get("LANGFUSE_URL", "")
    if langfuse_url:
        ok, detail = http_check(langfuse_url.rstrip("/") + "/api/public/health")
        rows.append((langfuse_url, "langfuse", "OK" if ok else "FAIL", detail))
        if not ok:
            fails += 1
    else:
        rows.append(("langfuse", "external", "SKIP", "LANGFUSE_URL пуст"))

    llm_base = env.get("APP_LLM_BASE_URL", "")
    if llm_base:
        url = llm_base.replace("host.docker.internal", "127.0.0.1").rstrip("/")
        ok, detail = http_check(url + "/models", host_header=None)
        rows.append((f"llm {env.get('APP_LLM_MODEL', '?')}", "external",
                     "OK" if ok else "FAIL", detail))
        if not ok:
            fails += 1
    else:
        rows.append(("llm", "external", "SKIP", "APP_LLM_BASE_URL пуст"))

    print()
    print(f"{'TARGET':<28} {'KIND':<10} {'RESULT':<6} DETAIL")
    print("-" * 72)
    for name, kind, res, detail in rows:
        print(f"{name:<28} {kind:<10} {res:<6} {detail}")
    print("-" * 72)

    if fails:
        print(f"\nИТОГ: FAIL ({fails} проверок упало)")
        sys.exit(1)
    print("\nИТОГ: OK - вся ожидаемая инфраструктура зелёная")


if __name__ == "__main__":
    main()