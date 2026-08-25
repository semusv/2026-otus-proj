"""Конфигурация приложения.

Все настройки — ТОЛЬКО через переменные окружения с префиксом ``APP_``
(pydantic-settings). Отсутствие обязательных переменных падает на старте
(fail-fast) с понятной ошибкой валидации.
"""

import os
from collections.abc import Mapping
from typing import Literal
from urllib.parse import quote_plus

from dotenv import dotenv_values
from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ENV_FILE = "../infra/.env"


def build_dsn(user: str, password: str, host: str, port: int, db: str) -> str:
    """Собирает DSN PostgreSQL для asyncpg, экранируя спецсимволы в кредах."""
    return (
        f"postgresql+asyncpg://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{db}"
    )


def load_env_file_into_environ(path: str = DEFAULT_ENV_FILE) -> None:
    """Доливает переменные из infra/.env в os.environ (setdefault: реальный env приоритетнее).

    Нужно для запуска CLI-скриптов (seed, миграции) вне docker-compose.
    В контейнере файла нет — операция no-op.
    """
    for key, value in dotenv_values(path).items():
        if value is not None:
            os.environ.setdefault(key, value)


class Settings(BaseSettings):
    """Типизированная модель конфигурации (префикс APP_*)."""

    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    env: Literal["dev", "stage", "prod"] = "dev"
    log_level: str = "INFO"

    # --- PostgreSQL (пользователи/роли/сессии/аудит) ---
    pg_host: str = "127.0.0.1"
    pg_port: int = Field(
        default=5432,
        validation_alias=AliasChoices("APP_PG_PORT", "APP_POSTGRES_PORT"),
    )
    pg_user: str
    pg_password: SecretStr
    pg_db: str

    # --- JWT (HS256, выпуск токенов самим backend'ом, см. ADR-007) ---
    jwt_secret: SecretStr
    jwt_ttl_minutes: int = 30

    # --- Метаданные API ---
    api_title: str = "GraphRAG Platform API"
    api_version: str = "0.3.0"

    @property
    def async_dsn(self) -> str:
        """DSN для SQLAlchemy async-движка (asyncpg)."""
        return build_dsn(
            self.pg_user,
            self.pg_password.get_secret_value(),
            self.pg_host,
            self.pg_port,
            self.pg_db,
        )

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


def required_env(name: str, env: Mapping[str, str]) -> str:
    value = env.get(name)
    if not value:
        raise RuntimeError(f"Отсутствует обязательная переменная окружения {name}")
    return value
