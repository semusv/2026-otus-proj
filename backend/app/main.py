"""Точка входа FastAPI-приложения (app factory)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from qdrant_client import AsyncQdrantClient

from app.agents.factory import build_chat_runtime
from app.api import api_router
from app.config import Settings
from app.core.errors import setup_error_handlers
from app.core.logging import configure_logging
from app.db.base import Database
from app.middleware.correlation import CorrelationIdMiddleware
from app.observability.tracing import setup_tracing
from app.rag.retrievers import GraphRetriever


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
    graph_retriever: GraphRetriever | None = getattr(app.state, "graph_retriever", None)
    if graph_retriever is not None:
        await graph_retriever.close()
    qdrant: AsyncQdrantClient | None = getattr(app.state, "qdrant_client", None)
    if qdrant is not None:
        await qdrant.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Собирает приложение; без обязательных APP_*-переменных падает на старте (fail-fast)."""
    cfg = settings or Settings()
    configure_logging(cfg.log_level)
    setup_tracing(enabled=cfg.tracing_enabled, endpoint=cfg.otel_exporter_endpoint)

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
            {"name": "admin", "description": "Управление ingestion корпуса (роль admin)"},
            {"name": "chat", "description": "Вопросы к GraphRAG-агенту (SSE-стриминг)"},
        ],
        lifespan=lifespan,
    )
    app.state.settings = cfg

    setup_error_handlers(app)
    app.add_middleware(CorrelationIdMiddleware)

    app.state.db = Database(cfg)

    chat_runtime, graph_retriever, qdrant_client = build_chat_runtime(cfg, app.state.db)
    app.state.chat_runtime = chat_runtime
    app.state.graph_retriever = graph_retriever
    app.state.qdrant_client = qdrant_client

    app.include_router(api_router)
    return app
