"""Схемы admin/ingest API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


RoleLiteral = Literal["viewer", "analyst", "admin"]


class UserCreate(BaseModel):
    """Создание учётной записи администратором."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"username": "qa_viewer", "password": "qa123456", "role": "viewer"}]
        }
    )

    username: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_.-]+$",
        description="Имя пользователя (уникально)",
    )
    password: str = Field(min_length=8, max_length=72, description="Пароль (bcrypt на сервере)")
    role: RoleLiteral = Field(default="viewer", description="Роль определяет метки доступа")


class UserOut(BaseModel):
    """Учётная запись без секретов."""

    user_id: str
    username: str
    role: RoleLiteral
    clearances: list[str] = Field(description="Метки доступа роли")
    is_active: bool
    created_at: datetime


class UsersListResponse(BaseModel):
    users: list[UserOut]
