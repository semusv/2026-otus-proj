"""Общие фикстуры тестов; окружение задаётся до импорта приложения."""

import os

os.environ.setdefault("APP_JWT_SECRET", "unit-test-secret-0123456789")
os.environ.setdefault("APP_PG_USER", "env_test_user")
os.environ.setdefault("APP_PG_PASSWORD", "env_test_pass")
os.environ.setdefault("APP_PG_DB", "env_test_db")

from collections.abc import Callable

import pytest
from app.config import Settings
from pydantic import SecretStr


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    """Фабрика валидного Settings с возможностью переопределить любые поля."""

    def _factory(**overrides: object) -> Settings:
        base: dict[str, object] = {
            "pg_user": "test_user",
            "pg_password": SecretStr("test_pass"),
            "pg_db": "test_db",
            "jwt_secret": SecretStr("test-jwt-secret-0123456789"),
            "jwt_ttl_minutes": 30,
        }
        base.update(overrides)
        return Settings(_env_file=None, **base)  # type: ignore[arg-type]

    return _factory


@pytest.fixture
def settings(settings_factory: Callable[..., Settings]) -> Settings:
    return settings_factory()
