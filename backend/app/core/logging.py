"""Структурное JSON-логирование с инъекцией trace_id/request_id в каждую строку."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.context import get_request_id, get_trace_id


class ContextFilter(logging.Filter):
    """Добавляет в каждую запись корреляционные идентификаторы из contextvar'ов."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """Форматирует запись в одну JSON-строку (структурный лог)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", "-"),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Настраивает корневой логгер на JSON-вывод в stdout (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    context_filter = ContextFilter()
    handler.addFilter(context_filter)

    root.handlers.clear()
    root.addHandler(handler)

    # Шумные библиотеки — только предупреждения и выше
    for noisy in ("sqlalchemy.engine", "asyncpg", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
