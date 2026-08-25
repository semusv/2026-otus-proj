"""Correlation-middleware: сквозные идентификаторы запроса.

- ``X-Trace-Id`` — входящий от клиента (или из ``traceparent``), иначе генерируется;
  всегда эхом возвращается в ответе;
- ``X-Request-Id`` — идентификатор конкретного HTTP-запроса (генерируется, если не передан).

Оба значения кладутся в contextvar'ы и попадают в каждую лог-строку.
Чистый ASGI-middleware (не BaseHTTPMiddleware) — безопасен для будущего SSE-стриминга.
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.datastructures import Headers, MutableHeaders

from app.core.context import set_request_id, set_trace_id

logger = logging.getLogger("app.access")

_SCOPE_HTTP = "http"


def _trace_from_headers(headers: Headers) -> str:
    trace_id = headers.get("x-trace-id")
    if trace_id:
        return trace_id
    traceparent = headers.get("traceparent")
    if traceparent:
        # формат W3C: 00-<32 hex trace-id>-<16 hex span-id>-01
        parts = traceparent.split("-")
        if len(parts) == 4 and len(parts[1]) == 32:
            return parts[1]
    return uuid.uuid4().hex


def _request_from_headers(headers: Headers) -> str:
    return headers.get("x-request-id") or uuid.uuid4().hex


class CorrelationIdMiddleware:
    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[Any]],
                       send: Callable[..., Awaitable[None]]) -> None:
        if scope["type"] != _SCOPE_HTTP:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        trace_id = _trace_from_headers(headers)
        request_id = _request_from_headers(headers)

        set_trace_id(trace_id)
        set_request_id(request_id)

        started = time.perf_counter()
        status_holder = {"status": 0}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                mutable = MutableHeaders(scope=message)
                mutable["X-Trace-Id"] = trace_id
                mutable["X-Request-Id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            path = scope.get("path", "-")
            method = scope.get("method", "-")
            logger.info(
                "%s %s -> %s (%.1f ms)",
                method,
                path,
                status_holder["status"],
                duration_ms,
            )
