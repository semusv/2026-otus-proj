"""Конфигурация приложения.

Все настройки — ТОЛЬКО через переменные окружения с префиксом ``APP_``
(pydantic-settings). Отсутствие обязательных переменных падает на старте
(fail-fast) с понятной ошибкой валидации.
"""

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Типизированная модель конфигурации (префикс APP_*)."""

    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file="../infra/.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["dev", "stage", "prod"] = "dev"
    log_level: str = "INFO"

    # --- PostgreSQL (пользователи/роли/сессии/аудит) ---
    pg_host: str = "127.0.0.1"
    pg_port: int = 5432
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
        return (
            f"postgresql+asyncpg://{self.pg_user}:{self.pg_password.get_secret_value()}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_db}"
        )

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"
