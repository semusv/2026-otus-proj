"""Unit-тесты fusion (RRF)."""

from app.rag.fusion import fuse_rankings
from app.rag.retrievers import RetrievedChunk


def make_chunk(act_id: str, chunk_no: int, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        act_id=act_id,
        title=f"Акт {act_id}",
        chunk_no=chunk_no,
        clearance="PUBLIC",
        text="текст",
        score=score,
    )


def test_vector_only_passthrough() -> None:
    hits = [make_chunk("a1", 0, 0.9)]
    assert fuse_rankings(hits, []) == hits


def test_rrf_boosts_items_in_both_rankings() -> None:
    vector = [make_chunk("a", 0, 0.8), make_chunk("b", 0, 0.7), make_chunk("c", 0, 0.6)]
    graph = [make_chunk("b", 0, 0.5)]
    fused = fuse_rankings(vector, graph)
    ids = [(chunk.act_id, chunk.chunk_no) for chunk in fused]
    # b присутствует в обоих ранжированиях -> поднимается выше c
    assert ids[0] == ("a", 0)
    assert ("b", 0) in ids[:2]


def test_dedup_prefers_vector_record() -> None:
    vector = [make_chunk("x", 3, 0.99)]
    graph = [RetrievedChunk(
        act_id="x",
        title="Другое название из графа",
        chunk_no=3,
        clearance="PUBLIC",
        text="другой текст",
        score=0.1,
    )]
    fused = fuse_rankings(vector, graph)
    assert len(fused) == 1
    assert fused[0].title == "Акт x"  # запись вектора имеет приоритет


def test_graph_only_items_appended() -> None:
    vector = [make_chunk("a", 0, 0.9)]
    graph = [make_chunk("g", 7, 0.4)]
    fused = fuse_rankings(vector, graph)
    assert [chunk.act_id for chunk in fused] == ["a", "g"]
