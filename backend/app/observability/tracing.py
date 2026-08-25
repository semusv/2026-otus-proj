"""OTel-трейсинг этапа 7: полный setup поверх минимума этапа 5.

Ключевая идея - ЕДИНЫЙ trace_id по всей цепочке: серверный span HTTP-запроса
создаётся в CorrelationIdMiddleware через ``server_span()``:

1. W3C ``traceparent`` извлекается пропагатором (клиент/фронт задал контекст);
2. иначе SpanContext собирается из ``X-Trace-Id`` (валидный 32-hex берётся как есть,
   произвольная строка хешируется sha256);
3. иначе SDK генерирует случайные идентификаторы (uuid4().hex из middleware всё равно
   попадает в логи как X-Trace-Id и совпадает с трейсом).

Итог: Jaeger trace id == X-Trace-Id == trace_id в логах.
Исходящие вызовы (LLM/Qdrant/Langfuse через httpx, включая OpenAI SDK, и SQLAlchemy)
инструментируются auto-instrumentation'ом - клиентские спаны attach'аются к текущему
контексту автоматически. Ручные спаны узлов LangGraph (traced_node) сохранены.
Экспорт - OTLP/HTTP в otel-collector (infra/otel-collector-config.yaml).
"""

import hashlib
import logging
import re
import secrets
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, TypeVar

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagate import extract
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import (
    NonRecordingSpan,
    Span,
    SpanContext,
    SpanKind,
    TraceFlags,
    Tracer,
    get_current_span,
    set_span_in_context,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger("app.observability")

_PROVIDER: TracerProvider | None = None
_INSTRUMENTED = False

_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")

T = TypeVar("T")


def normalize_trace_id(raw: str) -> str | None:
    """Приводит идентификатор к валидному 32-hex OTel trace id (или None).

    Валидный 32-hex проходит как есть (не ноль), произвольная строка хешируется
    sha256 - так любой клиентский X-Trace-Id становится корректным trace id.
    """
    value = (raw or "").strip()
    if not value:
        return None
    if _HEX32.match(value):
        lowered = value.lower()
        return None if int(lowered, 16) == 0 else lowered
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


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
    с OTLP/HTTP экспортёром на ``endpoint``. При enabled=False провайдер без
    процессоров: спаны создаются no-op'ом, экспорта нет.
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


def instrument_libraries(*, db_engine: "AsyncEngine") -> None:
    """Auto-instrumentation исходящих вызовов (идемпотентно, только при живом SDK).

    - httpx: классы Client/AsyncClient патчатся глобально - покрывает и OpenAI SDK,
      и qdrant-client (http-транспорт), и langfuse-экспортёры;
    - SQLAlchemy: спаны запросов на движке (sync_engine под капотом async-движка).
    """
    global _INSTRUMENTED
    if _PROVIDER is None or _INSTRUMENTED:
        return
    _INSTRUMENTED = True
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        logger.info("httpx инструментирован (исходящие LLM/Qdrant вызовы в трейсе)")
    except Exception as exc:  # pragma: no cover - защита от конфликтов версий
        logger.warning("httpx instrumentation не включён: %s", exc)
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument(engine=db_engine.sync_engine)
        logger.info("SQLAlchemy инструментирован (запросы к PG в трейсе)")
    except Exception as exc:  # pragma: no cover
        logger.warning("SQLAlchemy instrumentation не включён: %s", exc)


def get_tracer() -> Tracer:
    """Трейсер приложения (при выключенном SDK - no-op реализация)."""
    return trace.get_tracer("app")


@contextmanager
def server_span(
    method: str,
    path: str,
    headers: Mapping[str, Any],
    *,
    tracer: Tracer | None = None,
) -> Iterator[Span]:
    """Серверный span HTTP-запроса с единым trace_id (см. докстринг модуля).

    Приоритет родителя: W3C traceparent -> X-Trace-Id -> генерация SDK.
    ``tracer`` - точка подмены в тестах.
    """
    tr = tracer or get_tracer()
    carrier = {str(k).lower(): v for k, v in dict(headers).items()}
    ctx = extract(carrier)
    parent_sc = get_current_span(ctx).get_span_context()
    if not parent_sc.is_valid:
        normalized = normalize_trace_id(str(carrier.get("x-trace-id", "")))
        if normalized is not None:
            parent_sc = SpanContext(
                trace_id=int(normalized, 16),
                span_id=int(secrets.token_hex(8), 16),
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
        else:
            ctx = Context()
        if parent_sc.is_valid:
            ctx = set_span_in_context(NonRecordingSpan(parent_sc), ctx)
    with tr.start_as_current_span(
        "http.request",
        context=ctx,
        kind=SpanKind.SERVER,
        attributes={
            "http.request.method": method,
            "url.path": path,
            "client.trace_id_header": str(carrier.get("x-trace-id", "-")),
        },
    ) as span:
        yield span


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
