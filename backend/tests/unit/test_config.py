"""Тесты fail-fast валидации конфигурации (pydantic-settings)."""

from collections.abc import Callable

import pytest
from app.config import Settings
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def test_missing_jwt_secret_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("APP_JWT_SECRET", "APP_PG_USER", "APP_PG_PASSWORD", "APP_PG_DB"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    msg = str(exc.value)
    assert "jwt_secret" in msg
    assert "pg_user" in msg
    assert "pg_password" in msg
    assert "pg_db" in msg


def test_valid_settings_build_dsn(settings_factory: Callable[..., Settings]) -> None:
    cfg = settings_factory(pg_host="db.example", pg_port=6543)
    assert cfg.async_dsn == "postgresql+asyncpg://test_user:test_pass@db.example:6543/test_db"


def test_default_values(settings: Settings) -> None:
    assert settings.env == "dev"
    assert settings.log_level == "INFO"
    assert settings.jwt_ttl_minutes == 30


def test_invalid_env_value_rejected(settings_factory: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError):
        settings_factory(env="production")


def test_secret_not_leaked_in_repr(settings: Settings) -> None:
    assert "test-jwt-secret" not in repr(settings.jwt_secret)
    assert settings.jwt_secret.get_secret_value() == "test-jwt-secret-0123456789"
