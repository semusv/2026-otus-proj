"""Общие фикстуры тестов."""

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
