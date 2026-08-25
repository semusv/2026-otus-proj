"""Tools Interface агента (по заданию): реестр инструментов retrieval.

Planner выбирает имена инструментов, узел ``tools`` графа исполняет их через
этот класс. Каждый инструмент возвращает типизированный результат - состояние
графа остаётся сериализуемым и проверяемым (ADR-005).
"""

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

from app.config import Settings
from app.ingestion.embeddings import EmbeddingBackend
from app.rag.fusion import fuse_rankings
from app.rag.retrievers import GraphExpansion, GraphRetriever, RetrievedChunk, VectorRetriever

RETRIEVE_VEC = "retrieve_vec"
EXPAND_GRAPH = "expand_graph"
KNOWN_TOOLS = [RETRIEVE_VEC, EXPAND_GRAPH]


class SupportsEmbed(Protocol):
    """Точка подмены эмбеддера в тестах."""

    async def embed_query(self, text: str) -> list[float]: ...


class QueryEmbedder:
    """Асинхронная обёртка над синхронным Embedder'ом (bge-m3, CPU; ADR-009)."""

    def __init__(self, backend: EmbeddingBackend) -> None:
        self._backend = backend

    @property
    def dim(self) -> int:
        return self._backend.dim

    async def embed_query(self, text: str) -> list[float]:
        vectors = await asyncio.to_thread(self._backend.encode, [text])
        return vectors[0]


@dataclass
class ToolOutput:
    """Результат выполнения одного инструмента."""

    name: str
    chunks: list[RetrievedChunk] = field(default_factory=list)
    expansion: GraphExpansion | None = None


class AgentTools:
    """Инструменты агента поверх ретриверов; единая точка для моков в тестах."""

    def __init__(
        self,
        *,
        vector_retriever: VectorRetriever,
        graph_retriever: GraphRetriever,
        embedder: SupportsEmbed,
        settings: Settings,
    ) -> None:
        self.vector_retriever = vector_retriever
        self.graph_retriever = graph_retriever
        self.embedder = embedder
        self.settings = settings

    async def run(
        self,
        tool_names: list[str],
        query: str,
        *,
        clearances: list[str],
        seed_act_ids: list[str] | None = None,
    ) -> list[ToolOutput]:
        """Исполняет запланированные инструменты; порядок фиксирован для детерминизма."""
        outputs: list[ToolOutput] = []
        cfg = self.settings

        if RETRIEVE_VEC in tool_names:
            vector = await self.embedder.embed_query(query)
            hits = await self.vector_retriever.retrieve(
                vector,
                clearances=clearances,
                top_k=cfg.rag_vector_top_k,
            )
            outputs.append(ToolOutput(name=RETRIEVE_VEC, chunks=hits))
            if not seed_act_ids:
                seed_act_ids = [hit.act_id for hit in hits]

        if EXPAND_GRAPH in tool_names:
            expansion = await self.graph_retriever.expand(
                seed_act_ids or [],
                clearances=clearances,
                hops=cfg.rag_graph_hops,
            )
            neighbor_chunks = await self._neighbor_chunks(
                [act.id for act in expansion.related_acts],
                query=query,
                clearances=clearances,
            )
            outputs.append(
                ToolOutput(name=EXPAND_GRAPH, chunks=neighbor_chunks, expansion=expansion)
            )
        return outputs

    async def _neighbor_chunks(
        self, act_ids: list[str], *, query: str, clearances: list[str]
    ) -> list[RetrievedChunk]:
        """Лучший чанк каждого акта-соседа: кандидаты графа в общий пул fusion."""
        if not act_ids:
            return []
        vector = await self.embedder.embed_query(query)
        return await self.vector_retriever.retrieve_by_acts(
            vector,
            act_ids=act_ids[: self.settings.rag_vector_top_k * 2],
            clearances=clearances,
            top_k=max(len(act_ids), 1),
        )


__all__ = [
    "EXPAND_GRAPH",
    "RETRIEVE_VEC",
    "AgentTools",
    "QueryEmbedder",
    "ToolOutput",
    "fuse_rankings",
]
