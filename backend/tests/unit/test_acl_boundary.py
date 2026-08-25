"""Unit-тесты финального ACL-инварианта на границе API (этап 6).

Чистая функция enforce_acl_boundary + сквозной сценарий графа: инструмент
«сломался» и вернул SECRET-чанк при правах viewer - guardrail_out обязан
вырезать источник, понизить статус и пометить ответ note'ом.
"""

import pytest
from app.agents.graph import build_agent_graph
from app.agents.guardrails import ContextSource, enforce_acl_boundary
from app.rag.retrievers import RetrievedChunk

from tests.unit.test_agent_graph import BASE_STATE, FakeLLM, StubTools, make_runtime

ACT_PUB = "900100001"
ACT_SEC = "900100002"


def _source(act_id: str, clearance: str) -> ContextSource:
    return ContextSource(
        source_id="S1", act_id=act_id, title=f"Акт {act_id}",
        chunk_no=0, clearance=clearance, text="текст источника",
    )


class TestEnforceAclBoundary:
    def test_viewer_drops_secret_source_and_act(self) -> None:
        sources = [_source(ACT_PUB, "PUBLIC"), _source(ACT_SEC, "SECRET")]
        related = [
            {"id": ACT_PUB, "title": "Публичный", "clearance": "PUBLIC"},
            {"id": ACT_SEC, "title": "Секретный", "clearance": "SECRET"},
        ]

        result = enforce_acl_boundary(sources, related, ["PUBLIC"])

        assert [s.act_id for s in result.sources] == [ACT_PUB]
        assert [a["id"] for a in result.related_acts] == [ACT_PUB]
        assert result.dropped_ids == [ACT_SEC]
        assert result.violated

    def test_analyst_keeps_internal(self) -> None:
        sources = [_source(ACT_PUB, "PUBLIC"), _source("900100003", "INTERNAL")]

        result = enforce_acl_boundary(sources, [], ["INTERNAL", "PUBLIC"])

        assert len(result.sources) == 2
        assert not result.violated

    def test_admin_sees_everything(self) -> None:
        clearances = ["INTERNAL", "PUBLIC", "SECRET"]
        sources = [
            _source(ACT_PUB, "PUBLIC"),
            _source("900100003", "INTERNAL"),
            _source(ACT_SEC, "SECRET"),
        ]
        related = [{"id": ACT_SEC, "title": "Секретный", "clearance": "SECRET"}]

        result = enforce_acl_boundary(sources, related, clearances)

        assert len(result.sources) == 3
        assert len(result.related_acts) == 1
        assert not result.violated

    def test_missing_clearance_treated_as_denied(self) -> None:
        related = [{"id": ACT_SEC, "title": "Без метки"}]

        result = enforce_acl_boundary([], related, ["PUBLIC"])

        assert result.dropped_ids == [ACT_SEC]

    def test_empty_inputs_are_noop(self) -> None:
        result = enforce_acl_boundary([], [], ["PUBLIC"])

        assert not result.violated


@pytest.mark.unit
async def test_graph_drops_secret_source_despite_broken_tool(settings_factory) -> None:
    """Инструмент вернул SECRET-чанк viewer'у - граница API вырезает его из ответа."""
    secret_chunk = RetrievedChunk(
        act_id=ACT_SEC, title="О секретной методике", chunk_no=0,
        clearance="SECRET", text="Секретная методика шифрования.", score=0.95,
    )
    llm = FakeLLM()
    llm.stream_text = f"Пересказ секретного акта {ACT_SEC} [S1]."
    tools = StubTools([secret_chunk])
    runtime = make_runtime(settings_factory(), llm, tools)

    final = await build_agent_graph(runtime).ainvoke(dict(BASE_STATE))

    assert final["status"] == "degraded"
    notes = ",".join(final["notes"])
    assert "acl_violation_dropped" in notes and ACT_SEC in notes
    # единственный источник был SECRET - после вырезания цитат нет, ссылка удалена из текста
    assert final["citations"] == []
    assert "[S1]" not in str(final["answer"])
