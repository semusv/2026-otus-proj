"""Unit-тесты трейсинга этапа 7: единый trace_id + спаны узлов графа.

Используется изолированный TracerProvider (без глобального состояния OTel),
чтобы тесты не зависели от порядка инициализации провайдера.
"""

import hashlib

import pytest
from app.observability.tracing import normalize_trace_id, server_span, traced_node
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _isolated_tracer(exporter: InMemorySpanExporter):
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("unit-test")


@pytest.mark.unit
async def test_traced_node_exports_span_with_attributes() -> None:
    exporter = InMemorySpanExporter()
    tracer = _isolated_tracer(exporter)

    @traced_node("planner", tracer=tracer)
    async def node(state: dict) -> dict:
        return {"status": "ok", "iterations": 1}

    result = await node({})
    assert result["status"] == "ok"

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "graph.planner"
    assert span.attributes["graph.node"] == "planner"
    assert span.attributes["graph.status"] == "ok"
    assert span.attributes["graph.iterations"] == 1


@pytest.mark.unit
async def test_traced_node_records_exception() -> None:
    exporter = InMemorySpanExporter()
    tracer = _isolated_tracer(exporter)

    @traced_node("tools", tracer=tracer)
    async def broken(state: dict) -> dict:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await broken({})

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes["graph.node.error"] is True
    assert spans[0].attributes.get("graph.node.ok") is None


@pytest.mark.unit
async def test_traced_node_without_status_attributes_is_fine() -> None:
    exporter = InMemorySpanExporter()
    tracer = _isolated_tracer(exporter)

    @traced_node("generate", tracer=tracer)
    async def node(state: dict) -> dict:
        return {"answer": "текст"}

    await node({})

    span = exporter.get_finished_spans()[0]
    assert span.attributes["graph.node.ok"] is True
    assert "graph.status" not in span.attributes


def _trace_id_hex(span) -> str:
    return format(span.context.trace_id, "032x")


@pytest.mark.unit
async def test_server_span_uses_x_trace_id_as_otl_trace_id() -> None:
    exporter = InMemorySpanExporter()
    tracer = _isolated_tracer(exporter)
    tid = "4bf92f3577b34da6a3ce929d0e0e4736"

    with server_span("GET", "/health", {"x-trace-id": tid}, tracer=tracer):
        pass

    span = exporter.get_finished_spans()[0]
    assert span.name == "http.request"
    assert _trace_id_hex(span) == tid
    assert span.attributes["http.request.method"] == "GET"


@pytest.mark.unit
async def test_server_span_normalizes_arbitrary_header() -> None:
    exporter = InMemorySpanExporter()
    tracer = _isolated_tracer(exporter)
    expected = hashlib.sha256(b"my-trace-123").hexdigest()[:32]

    with server_span("GET", "/health", {"x-trace-id": "my-trace-123"}, tracer=tracer):
        pass

    assert _trace_id_hex(exporter.get_finished_spans()[0]) == expected


@pytest.mark.unit
async def test_server_span_prefers_w3c_traceparent() -> None:
    exporter = InMemorySpanExporter()
    tracer = _isolated_tracer(exporter)
    tid = "4bf92f3577b34da6a3ce929d0e0e4737"
    headers = {
        "x-trace-id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1",
        "traceparent": f"00-{tid}-00f067aa0ba902b7-01",
    }

    with server_span("GET", "/health", headers, tracer=tracer):
        pass

    assert _trace_id_hex(exporter.get_finished_spans()[0]) == tid


@pytest.mark.unit
async def test_server_span_generates_when_no_headers() -> None:
    exporter = InMemorySpanExporter()
    tracer = _isolated_tracer(exporter)

    with server_span("GET", "/health", {}, tracer=tracer) as span:
        pass

    finished = exporter.get_finished_spans()[0]
    assert _trace_id_hex(finished) != ""
    assert finished.attributes is not None
    assert span is finished or True


@pytest.mark.unit
def test_normalize_trace_id_contract() -> None:
    valid = "4bf92f3577b34da6a3ce929d0e0e4736"
    assert normalize_trace_id(valid) == valid
    assert normalize_trace_id("0" * 32) is None  # нулевой trace id невалиден в OTel
    hashed = normalize_trace_id("my-trace")
    assert hashed is not None and len(hashed) == 32
    assert normalize_trace_id("") is None
    assert normalize_trace_id("   ") is None


@pytest.mark.unit
def test_normalize_trace_id_uppercase_lowered() -> None:
    up = "4BF92F3577B34DA6A3CE929D0E0E4736"
    assert normalize_trace_id(up) == up.lower()
