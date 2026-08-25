"""База данных: декларативная база, движок, фабрика сессий."""

import uuid
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings


class Base(DeclarativeBase):
    """Общая декларативная база всех моделей."""


class Database:
    """Владелец движка и фабрики сессий (живёт в app.state)."""

    def __init__(self, settings: Settings) -> None:
        self.engine: AsyncEngine = create_async_engine(settings.async_dsn, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def dispose(self) -> None:
        await self.engine.dispose()


def new_pk() -> uuid.UUID:
    return uuid.uuid4()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI-зависимость: сессия БД на время запроса."""
    db: Database = request.app.state.db
    async with db.session_factory() as session:
        yield session
