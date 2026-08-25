"""Экспорт OpenAPI-контракта в docs/api/openapi.yaml (контракт коммитится).

Запуск (из корня): make openapi-export
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import yaml  # noqa: E402

from app.config import Settings, load_env_file_into_environ  # noqa: E402
from app.main import create_app  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "api" / "openapi.yaml"


def main() -> None:
    load_env_file_into_environ(str(BACKEND_DIR.parent / "infra" / ".env"))
    app = create_app(Settings())
    schema = app.openapi()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="\n") as f:
        yaml.dump(
            schema,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=120,
        )
    print(f"Контракт экспортирован: {OUT}")


if __name__ == "__main__":
    main()
