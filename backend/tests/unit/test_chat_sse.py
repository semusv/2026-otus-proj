"""Unit-тесты SSE-формата, QueueSink и planner'а."""

import json

import pytest
from app.agents.graph import NullSink, QueueSink
from app.agents.planner import fallback_replan, initial_plan
from app.schemas.chat import sse_format


@pytest.mark.unit
def test_sse_format_well_formed() -> None:
    raw = sse_format("token", {"delta": "привёт"})
    assert raw == 'event: token\ndata: {"delta": "привёт"}\n\n'
    data_line = raw.split("\ndata: ", 1)[1].strip()
    assert json.loads(data_line) == {"delta": "привёт"}


@pytest.mark.unit
async def test_queue_sink_delivers_events() -> None:
    sink = QueueSink()
    await sink.emit("status", {"stage": "planner"})
    await sink.emit("token", {"delta": "x"})
    assert sink.queue.qsize() == 2
    first = sink.queue.get_nowait()
    assert first == ("status", {"stage": "planner"})


@pytest.mark.unit
async def test_null_sink_is_noop() -> None:
    sink: NullSink = NullSink()
    await sink.emit("anything", {})  # не падает


@pytest.mark.unit
def test_initial_plan_uses_both_tools_and_original_query() -> None:
    plan = initial_plan("вопрос про закон")
    assert set(plan.tools) == {"retrieve_vec", "expand_graph"}
    assert plan.query == "вопрос про закон"


@pytest.mark.unit
def test_fallback_replan_adds_refined_query() -> None:
    plan = fallback_replan(
        previous_tools=["retrieve_vec"],
        question="исходный",
        refined_query="уточнённый запрос  \u200bс мусором",
    )
    assert plan.query.startswith("уточнённый запрос")
    assert "\u200b" not in plan.query
    # недостающий инструмент добавляется
    assert set(plan.tools) == {"retrieve_vec", "expand_graph"}
