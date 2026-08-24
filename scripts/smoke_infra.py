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

SERVICES_WITHOUT_HCHECK = {"jaeger", "grafana", "otel-collector"}

DIRECT_HTTP_CHECKS = [
    ("backend", "/health"),
    ("qdrant", "/healthz"),
]

TRAEFIK_HTTP_CHECKS = [
    ("api.localhost", "/health", "backend"),
    ("neo4j.localhost", "/", "neo4j"),
    ("jaeger.localhost", "/", "jaeger"),
    ("prometheus.localhost", "-/healthy", "prometheus"),
    ("grafana.localhost", "/api/health", "grafana"),
]


def compose_cmd(args: argparse.Namespace) -> list[str]:
    cmd = ["docker", "compose", "--project-directory", str(COMPOSE_DIR),
           "-f", str(COMPOSE_DIR / "docker-compose.yml")]
    if args.with_obs:
        cmd += ["-f", str(COMPOSE_DIR / "docker-compose.observability.yml")]
    if args.with_gpu:
        cmd += ["-f", str(COMPOSE_DIR / "docker-compose.gpu.yml")]
    return cmd


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


def get_compose_ps(cmd_prefix: list[str]) -> dict[str, dict]:
    result = subprocess.run(
        cmd_prefix + ["ps", "--format", "json"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"FAIL: docker compose ps error:\n{result.stderr}")
        sys.exit(1)
    services = {}
    out = result.stdout.strip()
    if not out:
        return services
    try:
        data = json.loads(out)
        items = data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        items = [json.loads(line) for line in out.splitlines() if line.strip()]
    for item in items:
        name = item.get("Service") or item.get("Name") or ""
        services[name] = item
    return services


def check_containers(expected: list[str], running: dict[str, dict]) -> tuple[list, int]:
    rows, fails = [], 0
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
    extra = set(running) - set(expected)
    if extra:
        rows.append((",".join(sorted(extra)), "container", "WARN",
                     "запущены, но не входят в выбранный набор"))
    return rows, fails


def http_check(url: str, host_header: str | None = None) -> tuple[bool, str]:
    headers = {"Host": host_header} if host_header else {}
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
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
    args = parser.parse_args()

    expected = list(BASE_SERVICES)
    if args.with_obs:
        expected += OBS_SERVICES
    if args.with_gpu:
        expected += GPU_SERVICES

    prefix = compose_cmd(args)
    running = get_compose_ps(prefix)

    rows, fails = check_containers(expected, running)

    traefik_port = load_env(COMPOSE_DIR / ".env").get("APP_TRAEFIK_HTTP_PORT", "80")

    # прямые проверки берём порты по умолчанию из .env
    env = load_env(COMPOSE_DIR / ".env")
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

    langfuse_url = env.get("LANGFUSE_URL", "")
    if langfuse_url:
        ok, detail = http_check(langfuse_url.rstrip("/") + "/api/public/health")
        rows.append((langfuse_url, "langfuse", "OK" if ok else "FAIL", detail))
        if not ok:
            fails += 1
    else:
        rows.append(("langfuse", "external", "SKIP", "LANGFUSE_URL пуст"))

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
