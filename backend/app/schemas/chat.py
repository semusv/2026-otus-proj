"""Схемы контракта POST /api/chat."""

import json
import uuid
from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Запрос к агенту; ``stream=False`` возвращает обычный JSON (Postman/нагрузка)."""

    message: str = Field(
        min_length=1,
        max_length=8000,
        description="Вопрос пользователя (санитайзер и guardrails применяются на сервере)",
        examples=["Какие требования к раскрытию информации эмитентами?"],
    )
    session_id: uuid.UUID | None = Field(
        default=None,
        description="Идентификатор диалога; None - начать новый диалог",
    )
    stream: bool = Field(default=True, description="true - SSE-поток событий, false - JSON")


class CitationOut(BaseModel):
    """Цитата ответа со ссылкой на источник (только из разрешённого контекста)."""

    source_id: str = Field(description="Маркер источника [S1] из текста ответа")
    act_id: str = Field(description="ID акта в корпусе/графе")
    title: str = Field(description="Название акта")
    chunk_no: int = Field(description="Номер чанка внутри акта")
    clearance: str = Field(description="Метка доступа источника")


class ChatResponse(BaseModel):
    """Ответ чата в non-stream режиме (структура совпадает с событием ``done`` SSE)."""

    session_id: uuid.UUID
    answer: str
    citations: list[CitationOut]
    status: Literal["ok", "degraded", "empty"]
    replans: int = Field(default=0, description="Число выполненных re-plan итераций агента")
    related_acts: list[dict[str, str]] = Field(
        default_factory=list, description="Соседние акты графа (путь по графу для фронта)"
    )
    notes: list[str] = Field(default_factory=list)
    trace_id: str | None = None


SSE_EVENTS_DESCRIPTION = (
    "SSE-поток (media type text/event-stream), события в порядке возникновения:\n\n"
    "- `status` - этап конвейера: `{\"stage\": \"guardrails|planner|retrieve|fusion_rerank|"
    "generate|evaluate|guardrails_out\", \"iteration\": N?, \"tools\": [...]?}`\n"
    "- `token` - дельта генерации: `{\"delta\": \"...\"}`\n"
    "- `done` - финал: структура как у ChatResponse\n"
    "- `error` - ошибка обработки: `{\"code\": \"...\", \"message\": \"...\"}`\n\n"
    "Каждое событие формата `event: <имя>\\ndata: <JSON>\\n\\n`."
)


def sse_format(event: str, data: dict[str, object]) -> str:
    """Форматирует одно SSE-событие."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
