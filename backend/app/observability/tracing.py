"""Минимальный OTel-трейсинг этапа 5: ручные спаны на узлах LangGraph -> Jaeger.

Полные три столпа наблюдаемости (auto-instrumentation, метрики, логи) - этап 7;
здесь только то, что требует acceptance: «в трейсе видны шаги графа включая re-plan».
Экспорт - OTLP/HTTP в otel-collector (infra/otel-collector-config.yaml).
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Tracer

logger = logging.getLogger("app.observability")

_PROVIDER: TracerProvider | None = None

T = TypeVar("T")


def setup_tracing(
    *,
    enabled: bool,
    endpoint: str,
    service_name: str = "graphrag-backend",
    exporter: Any | None = None,
) -> None:
    """Инициализирует глобальный TracerProvider (идемпотентно).

    ``exporter`` - точка подмены в тестах: переданный экспортёр подключается через
    SimpleSpanProcessor (синхронно, без фонового потока). Прод-путь - BatchSpanProcessor
    с OTLP/HTTP экспортёром на ``endpoint``.
    """
    global _PROVIDER
    if _PROVIDER is not None:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if enabled:
        if exporter is not None:
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor

            provider.add_span_processor(SimpleSpanProcessor(exporter))
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        logger.info("OTel-трейсинг включён: экспорт в %s", endpoint)
    else:
        logger.info("OTel-трейсинг отключён (APP_TRACING_ENABLED=false)")
    trace.set_tracer_provider(provider)
    _PROVIDER = provider


def get_tracer() -> Tracer:
    """Трейсер приложения (при выключенном SDK - no-op реализация)."""
    return trace.get_tracer("app.agents")


def traced_node(
    name: str, *, tracer: Tracer | None = None
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Декоратор узла LangGraph: каждый вызов узла = отдельный span с атрибутами статуса.

    ``tracer`` - точка подмены в тестах (изолированный провайдер без глобального состояния).
    """
    tr = tracer or get_tracer()

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        async def wrapper(state: dict[str, Any]) -> T:
            with tr.start_as_current_span(f"graph.{name}") as span:
                span.set_attribute("graph.node", name)
                try:
                    result = await func(state)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_attribute("graph.node.error", True)
                    raise
                updates = result if isinstance(result, dict) else {}
                status = updates.get("status")
                if isinstance(status, str):
                    span.set_attribute("graph.status", status)
                iterations = updates.get("iterations")
                if isinstance(iterations, int):
                    span.set_attribute("graph.iterations", iterations)
                span.set_attribute("graph.node.ok", True)
                return result

        return wrapper

    return decorator
