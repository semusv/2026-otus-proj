"""Contextvar'ы корреляции запроса (request_id / trace_id / user_id).

Заполняются CorrelationIdMiddleware (идентификаторы) и auth-зависимостью (user_id),
читаются logging-filter'ом и кодом приложения, чтобы каждая лог-строка несла
сквозные идентификаторы (этап 7).
"""

from contextvars import ContextVar

_UNSET = "-"

_request_id: ContextVar[str] = ContextVar("request_id", default=_UNSET)
_trace_id: ContextVar[str] = ContextVar("trace_id", default=_UNSET)
_user_id: ContextVar[str] = ContextVar("user_id", default=_UNSET)


def get_request_id() -> str:
    return _request_id.get()


def get_trace_id() -> str:
    return _trace_id.get()


def get_user_id() -> str:
    return _user_id.get()


def set_request_id(value: str) -> None:
    _request_id.set(value)


def set_trace_id(value: str) -> None:
    _trace_id.set(value)


def set_user_id(value: str) -> None:
    _user_id.set(value)
