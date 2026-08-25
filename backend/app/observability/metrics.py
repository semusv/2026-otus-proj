"""Метрики Prometheus этапа 7 (ADR-008): HTTP + бизнес-метрики чата/графа.

- HTTP: RPS/latency per route-template (исключая /health,/metrics - самоскрейп и liveness
  не должны создавать шум);
- Бизнес: статусы чата (ok/degraded/empty), события guardrails, длительность узлов LangGraph,
  итерации агента, размер контекста после rerank.
Токены/сек приходят нативно с vLLM (job vllm в prometheus.yml).
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Histogram,
    generate_latest,
)

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "HTTP запросы по маршрутам",
    ["method", "route", "status"],
)
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Длительность HTTP запросов по маршрутам",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)
CHAT_STATUS_TOTAL = Counter(
    "chat_status_total",
    "Итоговые статусы ходов чата",
    ["status"],
)
GUARDRAIL_EVENTS_TOTAL = Counter(
    "guardrail_events_total",
    "Значимые события guardrails",
    ["kind"],
)
GRAPH_NODE_DURATION = Histogram(
    "graph_node_duration_seconds",
    "Длительность узлов графа агента",
    ["node"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0),
)
AGENT_ITERATIONS = Histogram(
    "agent_iterations",
    "Число проходов planner за ход (1 + re-plan)",
    buckets=(1, 2, 3, 4, 5, 6),
)
RAG_CONTEXT_CHUNKS = Histogram(
    "rag_context_chunks",
    "Размер контекста генерации (чанков)",
    buckets=(1, 2, 3, 5, 8, 12, 16, 24, 32),
)

_SKIP_PATHS = {"/metrics", "/health"}


def render_metrics() -> tuple[bytes, str]:
    """Тело и content-type для GET /metrics."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def observe_chat_status(status: str) -> None:
    CHAT_STATUS_TOTAL.labels(status=status).inc()


def observe_guardrail_event(kind: str) -> None:
    GUARDRAIL_EVENTS_TOTAL.labels(kind=kind).inc()


def observe_agent_turn(*, iterations: int, context_chunks: int) -> None:
    AGENT_ITERATIONS.observe(max(1, iterations))
    RAG_CONTEXT_CHUNKS.observe(context_chunks)


class MetricsMiddleware:
    """Чистый ASGI-middleware: RPS/latency по route-template.

    Route-template берётся из scope["route"].path ПОСЛЕ вызова (FastAPI кладёт
    совпавший APIRoute); несматченные запросы помечаются "unmatched".
    """

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[Any]],
                       send: Callable[..., Awaitable[None]]) -> None:
        if scope["type"] != "http" or scope.get("path") in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "-")
        started = time.perf_counter()
        status_holder = {"status": 0}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = getattr(scope.get("route"), "path", "unmatched")
            elapsed = time.perf_counter() - started
            HTTP_REQUESTS_TOTAL.labels(
                method=method, route=route, status=str(status_holder["status"])
            ).inc()
            HTTP_REQUEST_DURATION.labels(method=method, route=route).observe(elapsed)


__all__ = [
    "MetricsMiddleware",
    "observe_agent_turn",
    "observe_chat_status",
    "observe_guardrail_event",
    "render_metrics",
]
