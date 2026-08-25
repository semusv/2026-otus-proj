"""Интеграционные тесты: изолированная БД graphrag_itg_* в PG из compose + миграции.

Требует запущенный compose (docker compose up -d postgres). Запуск: make test-integration
"""

import asyncio
import uuid as uuidlib
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config
from app.config import Settings
from app.main import create_app
from sqlalchemy.ext.asyncio import AsyncSession

BACKEND_DIR = Path(__file__).resolve().parents[2]
INFRA_ENV = BACKEND_DIR.parent / "infra" / ".env"


def _replace_db(dsn: str, dbname: str) -> str:
    return dsn.rsplit("/", 1)[0] + "/" + dbname


def _pg_native_dsn(sqlalchemy_dsn: str) -> str:
    return sqlalchemy_dsn.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture(scope="session")
def base_settings() -> Settings:
    from app.config import load_env_file_into_environ

    load_env_file_into_environ(str(INFRA_ENV))
    return Settings(_env_file=None)


@pytest.fixture(scope="session")
def pg_dsn(base_settings: Settings) -> Iterator[str]:
    """Создаёт уникальную тестовую БД, применяет миграции, удаляет после сессии."""
    dbname = f"graphrag_itg_{uuidlib.uuid4().hex[:8]}"
    admin_dsn = _pg_native_dsn(_replace_db(base_settings.async_dsn, "postgres"))
    test_dsn = _replace_db(base_settings.async_dsn, dbname)

    async def _provision() -> None:
        conn = await asyncpg.connect(admin_dsn)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{dbname}"')
        finally:
            await conn.close()

    async def _teardown() -> None:
        conn = await asyncpg.connect(admin_dsn)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        finally:
            await conn.close()

    asyncio.run(_provision())
    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    alembic_cfg.set_main_option("sqlalchemy.url", test_dsn)
    command.upgrade(alembic_cfg, "head")

    yield test_dsn

    asyncio.run(_teardown())


@pytest.fixture
async def itg_client(
    base_settings: Settings,
    pg_dsn: str,
) -> AsyncIterator[tuple[httpx.AsyncClient, object]]:
    """HTTP-клиент поверх приложения, смотрящего на тестовую БД."""
    test_dbname = pg_dsn.rsplit("/", 1)[1]
    settings = base_settings.model_copy(update={"pg_db": test_dbname})
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, app


@pytest.fixture
async def db_factory(
    itg_client: tuple[httpx.AsyncClient, object],
) -> AsyncIterator[Callable[[], AsyncSession]]:
    """Фабрика сессий БД приложения (для прямых проверок таблиц)."""
    _, app = itg_client
    session_factory = app.state.db.session_factory  # type: ignore[attr-defined]

    def _make() -> AsyncSession:
        return session_factory()

    yield _make
