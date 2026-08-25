"""Unit-тесты минимального трейсинга: спаны узлов графа попадают в экспортёр.

Используется изолированный TracerProvider (без глобального состояния OTel),
чтобы тесты не зависели от порядка инициализации провайдера.
"""

import pytest
from app.observability.tracing import traced_node
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
