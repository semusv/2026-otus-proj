"""Схемы admin/ingest API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class IngestStartResponse(BaseModel):
    state: Literal["started"] = "started"
    message: str = "Прогон ingestion запущен"


class IngestStatusResponse(BaseModel):
    state: Literal["idle", "running", "done", "error"]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stats: dict[str, Any] | None = None
    error: str | None = None


class QdrantStats(BaseModel):
    """Наполнение векторного хранилища."""

    collection: str
    points: int


class Neo4jStats(BaseModel):
    """Узлы и рёбра графа знаний (по меткам онтологии ADR-006)."""

    acts: int
    authorities: int
    topics: int
    concepts: int
    relationships: int


class PostgresStats(BaseModel):
    """Учётные записи и история диалогов."""

    users: int
    chat_sessions: int
    chat_messages: int


class StorageStatsResponse(BaseModel):
    """Снимок текущего наполнения хранилищ (без прогресса прогонов)."""

    qdrant: QdrantStats
    neo4j: Neo4jStats
    postgres: PostgresStats
