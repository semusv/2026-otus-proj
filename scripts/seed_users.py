"""Сидинг демо-пользователей: viewer / analyst / admin.

Запуск (из корня репозитория): make seed-users
Пароли по умолчанию — только для локальной разработки; переопределяются
флагами --viewer-password/--analyst-password/--admin-password.
"""

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings, load_env_file_into_environ  # noqa: E402
from app.db.seed import DEFAULT_PASSWORDS, seed  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Сидинг ролей и пользователей")
    for role in ("viewer", "analyst", "admin"):
        parser.add_argument(f"--{role}-password", default=None)
    args = parser.parse_args()

    load_env_file_into_environ(str(BACKEND_DIR.parent / "infra" / ".env"))
    settings = Settings()

    passwords = dict(DEFAULT_PASSWORDS)
    for role in ("viewer", "analyst", "admin"):
        override = getattr(args, f"{role}_password")
        if override:
            passwords[role] = override

    result = asyncio.run(seed(settings, passwords))
    print(f"Готово. Созданы: {result['created']}; обновлены: {result['updated']}")


if __name__ == "__main__":
    main()
