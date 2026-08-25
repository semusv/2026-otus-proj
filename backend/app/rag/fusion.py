"""Fusion кандидатов из разных источников (RRF - Reciprocal Rank Fusion).

Чистые функции без I/O: легко покрываются unit-тестами (этап 5).
"""

from app.rag.retrievers import RetrievedChunk


def fuse_rankings(
    vector_hits: list[RetrievedChunk],
    graph_context: list[RetrievedChunk],
    *,
    k: int = 60,
) -> list[RetrievedChunk]:
    """Сливает ранжирования вектора и графа в один пул без дублей.

    RRF: score = sum(1 / (k + rank_i)). Графовые соседи не имеют естественного
    релеванс-скоринга, поэтому входят одним «виртуальным» ранжированием
    (порядок = порядок возврата Cypher). Дубли по (act_id, chunk_no) схлопываются,
    приоритет у записи из более надёжного источника (вектор).
    """
    if not graph_context:
        return list(vector_hits)

    scores: dict[tuple[str, int], float] = {}
    best: dict[tuple[str, int], RetrievedChunk] = {}

    for rank, chunk in enumerate(vector_hits):
        key = (chunk.act_id, chunk.chunk_no)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
        best.setdefault(key, chunk)
    for rank, chunk in enumerate(graph_context):
        key = (chunk.act_id, chunk.chunk_no)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
        best.setdefault(key, chunk)

    ordered_keys = sorted(scores, key=lambda key: scores[key], reverse=True)
    return [best[key] for key in ordered_keys]
