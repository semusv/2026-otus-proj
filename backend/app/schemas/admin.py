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
