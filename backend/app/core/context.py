"""Contextvar'ы корреляции запроса (request_id / trace_id).

Заполняются CorrelationIdMiddleware, читаются logging-filter'ом и кодом приложения,
чтобы каждая лог-строка несла сквозные идентификаторы.
"""

from contextvars import ContextVar

_UNSET = "-"

_request_id: ContextVar[str] = ContextVar("request_id", default=_UNSET)
_trace_id: ContextVar[str] = ContextVar("trace_id", default=_UNSET)


def get_request_id() -> str:
    return _request_id.get()


def get_trace_id() -> str:
    return _trace_id.get()


def set_request_id(value: str) -> None:
    _request_id.set(value)


def set_trace_id(value: str) -> None:
    _trace_id.set(value)
