"""Выгрузка секретов приложения из HashiCorp Vault KV v2 в env-файл.

Используется initContainer'ом backend-деплоя в minikube/Helm (этап 10):
initContainer рендерит /config/secrets.env, основной контейнер выполняет
`. /config/secrets.env` перед запуском uvicorn - дальше работает обычный
pydantic-settings. Вне k8s (compose/хост) модуль не применяется: секреты
приходят обычным env.

Запуск:
    python -m app.infra.vault_fetch --out /config/secrets.env

Переменные окружения:
    VAULT_ADDR    адрес Vault, напр. http://vault:8200
    VAULT_TOKEN   токен (dev-mode root; прод-эволюция: AppRole/K8s-auth)
    VAULT_KV_PATH путь KV v2 без data/, напр. secret/graphrag/app

Ретраи нужны потому, что поды поднимаются параллельно: vault может быть
ещё не готов, seed-Job мог ещё не положить секреты (404). Ровно поэтому
же после успеха проверяем непустой набор APP_* ключей.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import httpx

DEFAULT_RETRIES = 12
DEFAULT_SLEEP_SECONDS = 5.0


def api_path(kv_path: str) -> str:
    """secret/graphrag/app -> v1/secret/data/graphrag/app (KV v2 API)."""
    mount, _, rest = kv_path.strip("/").partition("/")
    if not mount or not rest:
        raise ValueError(f"Ожидался путь вида '<mount>/<path>...', получено: {kv_path!r}")
    return f"v1/{mount}/data/{rest}"


def render_env(secrets: Mapping[str, object]) -> str:
    """APP_*-ключи -> строки KEY='value' (безопасно для `. file`)."""
    lines = []
    for key in sorted(secrets):
        if not key.startswith("APP_"):
            continue
        value = str(secrets[key])
        escaped = value.replace("'", "'\\''")
        lines.append(f"{key}='{escaped}'")
    return "\n".join(lines) + ("\n" if lines else "")


def fetch(client: httpx.Client, addr: str, kv_path: str) -> dict[str, object]:
    """Одноразовый GET секрета; KeyError/HTTP-статусы обрабатывает вызывающий."""
    url = f"{addr.rstrip('/')}/{api_path(kv_path)}"
    response = client.get(url)
    response.raise_for_status()
    payload = response.json()
    data: dict[str, object] = payload["data"]["data"]
    return data


def run(
    out_path: str | Path,
    addr: str,
    token: str,
    kv_path: str,
    *,
    retries: int = DEFAULT_RETRIES,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, object]:
    """Читает секрет с ретраями и пишет env-файл. Возвращает выгруженные ключи."""
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with httpx.Client(
                transport=transport, timeout=10.0, headers={"X-Vault-Token": token}
            ) as client:
                secrets = fetch(client, addr, kv_path)
            rendered = render_env(secrets)
            if not rendered:
                raise LookupError("Секрет прочитан, но APP_*-ключей нет (seed ещё не отработал?)")
            Path(out_path).write_text(rendered, encoding="utf-8")
            print(f"vault_fetch: выгружено {len(rendered.splitlines())} секретов -> {out_path}")
            return secrets
        except (httpx.HTTPError, KeyError, LookupError, ValueError) as exc:
            last_error = exc
            print(f"vault_fetch: попытка {attempt}/{retries} не удалась: {exc}")
            if attempt < retries:
                time.sleep(sleep_seconds)
    raise RuntimeError(
        f"vault_fetch: не удалось получить секрет за {retries} попыток: {last_error}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Выгрузка APP_* секретов из Vault в env-файл"
    )
    parser.add_argument(
        "--out", default="/config/secrets.env", help="путь результирующего env-файла"
    )
    args = parser.parse_args(argv)

    addr = os.environ.get("VAULT_ADDR", "")
    token = os.environ.get("VAULT_TOKEN", "")
    kv_path = os.environ.get("VAULT_KV_PATH", "")
    required = (("VAULT_ADDR", addr), ("VAULT_TOKEN", token), ("VAULT_KV_PATH", kv_path))
    missing = [name for name, value in required if not value]
    if missing:
        print(f"vault_fetch: не заданы переменные: {', '.join(missing)}", file=sys.stderr)
        return 2

    try:
        run(args.out, addr, token, kv_path)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
