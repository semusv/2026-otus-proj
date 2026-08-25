"""Точка входа FastAPI-приложения (app factory)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import api_router
from app.config import Settings
from app.core.errors import setup_error_handlers
from app.core.logging import configure_logging
from app.db.base import Database
from app.middleware.correlation import CorrelationIdMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logging.getLogger("app").info(
        "Старт приложения: env=%s, версия API=%s",
        app.state.settings.env,
        app.state.settings.api_version,
    )
    yield
    db: Database | None = getattr(app.state, "db", None)
    if db is not None:
        await db.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Собирает приложение; без обязательных APP_*-переменных падает на старте (fail-fast)."""
    cfg = settings or Settings()
    configure_logging(cfg.log_level)

    app = FastAPI(
        title=cfg.api_title,
        version=cfg.api_version,
        description=(
            "Платформа мультимодального анализа корпоративных знаний "
            "(GraphRAG, закрытый контур). Контракт экспортируется в docs/api/openapi.yaml."
        ),
        openapi_tags=[
            {"name": "system", "description": "Здоровье сервиса и метрики"},
            {"name": "auth", "description": "Аутентификация и профиль пользователя"},
        ],
        lifespan=lifespan,
    )
    app.state.settings = cfg

    setup_error_handlers(app)
    app.add_middleware(CorrelationIdMiddleware)

    app.state.db = Database(cfg)

    app.include_router(api_router)
    return app
