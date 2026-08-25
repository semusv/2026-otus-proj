"""Retrieval-инструменты агента: векторный поиск (Qdrant) и расширение графа (Neo4j).

Security-by-Design (ADR-007): фильтр ``clearance IN allowed`` применяется ВНУТРИ
запросов к хранилищам (pre-fetch ACL) - запрещённые данные физически не покидают БД.
"""

from dataclasses import dataclass, field
from typing import Any

from neo4j import AsyncGraphDatabase
from qdrant_client import AsyncQdrantClient, models

GRAPH_REL_TYPES = "REFERENCES|HAS_TOPIC|MENTIONS"


@dataclass(frozen=True)
class RetrievedChunk:
    """Чанк из Qdrant до присвоения source_id (нумерация после rerank)."""

    act_id: str
    title: str
    chunk_no: int
    clearance: str
    text: str
    score: float


@dataclass(frozen=True)
class RelatedAct:
    """Акт-сосед по графу (для «пути графа» на фронте)."""

    id: str
    title: str
    clearance: str


@dataclass(frozen=True)
class GraphExpansion:
    """Результат расширения графа: контекстные факты + метаданные для фронта."""

    related_acts: list[RelatedAct] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)

    def as_context_text(self) -> str:
        """Человекочитаемый блок для промпта генерации."""
        lines: list[str] = []
        if self.related_acts:
            acts = "; ".join(
                f"«{act.title}» (id={act.id}, {act.clearance})" for act in self.related_acts
            )
            lines.append(f"Связанные нормативные акты: {acts}.")
        if self.topics:
            lines.append(f"Темы, к которым относятся найденные акты: {', '.join(self.topics)}.")
        if self.concepts:
            lines.append(
                f"Юридические понятия из связных актов: {', '.join(self.concepts)}."
            )
        return "\n".join(lines)


class VectorRetriever:
    """Векторный поиск по коллекции chunks с pre-fetch ACL-фильтром."""

    def __init__(self, client: AsyncQdrantClient, collection: str) -> None:
        self._client = client
        self._collection = collection

    async def retrieve(
        self,
        query_vector: list[float],
        *,
        clearances: list[str],
        top_k: int,
    ) -> list[RetrievedChunk]:
        result = await self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=top_k,
            with_payload=True,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="clearance",
                        match=models.MatchAny(any=clearances),
                    )
                ]
            ),
        )
        chunks: list[RetrievedChunk] = []
        for point in result.points:
            payload: dict[str, Any] = dict(point.payload or {})
            chunks.append(
                RetrievedChunk(
                    act_id=str(payload.get("act_id", "")),
                    title=str(payload.get("title", "")),
                    chunk_no=int(payload.get("chunk_no", 0)),
                    clearance=str(payload.get("clearance", "")),
                    text=str(payload.get("text", "")),
                    score=float(point.score),
                )
            )
        return chunks

    async def retrieve_by_acts(
        self,
        query_vector: list[float],
        *,
        act_ids: list[str],
        clearances: list[str],
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Поиск, ограниченный множеством актов (кандидаты графового расширения).

        ACL-фильтр по clearance сохраняется - соседство в графе не расширяет права.
        """
        if not act_ids:
            return []
        result = await self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=top_k,
            with_payload=True,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="clearance",
                        match=models.MatchAny(any=clearances),
                    ),
                    models.FieldCondition(
                        key="act_id",
                        match=models.MatchAny(any=act_ids),
                    ),
                ]
            ),
        )
        chunks: list[RetrievedChunk] = []
        for point in result.points:
            payload = dict(point.payload or {})
            chunks.append(
                RetrievedChunk(
                    act_id=str(payload.get("act_id", "")),
                    title=str(payload.get("title", "")),
                    chunk_no=int(payload.get("chunk_no", 0)),
                    clearance=str(payload.get("clearance", "")),
                    text=str(payload.get("text", "")),
                    score=float(point.score),
                )
            )
        return chunks


class GraphRetriever:
    """Расширение графа от актов-сидов: REFERENCES-соседи + понятия/темы (1-2 hop).

    Фильтр clearance проверяется на каждом Act вдоль пути - соседний SECRET-акт
    не виден даже через цепочку ссылок (краеугольный сценарий этапа 6).
    """

    def __init__(self, uri: str, user: str, password: str, *, max_neighbors: int = 8,
                 max_terms: int = 15) -> None:
        self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        self._max_neighbors = max_neighbors
        self._max_terms = max_terms

    async def close(self) -> None:
        await self._driver.close()

    async def expand(
        self,
        seed_act_ids: list[str],
        *,
        clearances: list[str],
        hops: int,
    ) -> GraphExpansion:
        if not seed_act_ids:
            return GraphExpansion()
        hops = min(max(hops, 1), 2)

        async with self._driver.session() as session:
            related = await session.run(
                f"""
                MATCH path = (a:Act)-[:{GRAPH_REL_TYPES}*1..{hops}]-(b:Act)
                WHERE a.id IN $seeds
                  AND all(n IN [x IN nodes(path) WHERE x:Act] WHERE n.clearance IN $allowed)
                  AND b.clearance IN $allowed
                RETURN DISTINCT b.id AS id, b.title AS title, b.clearance AS clearance
                LIMIT $limit
                """,
                seeds=seed_act_ids,
                allowed=clearances,
                limit=self._max_neighbors,
            )
            act_rows: list[dict[str, Any]] = await related.data()

            term_result = await session.run(
                """
                MATCH (a:Act)-[:MENTIONS|HAS_TOPIC]->(t)
                WHERE a.id IN $act_ids
                RETURN labels(t)[0] AS kind, t.name AS name, count(*) AS cnt
                ORDER BY cnt DESC
                LIMIT $limit
                """,
                act_ids=seed_act_ids + [row["id"] for row in act_rows],
                limit=self._max_terms,
            )
            term_rows: list[dict[str, Any]] = await term_result.data()

        related_acts = [
            RelatedAct(id=row["id"], title=row.get("title") or row["id"],
                       clearance=row.get("clearance", ""))
            for row in act_rows
        ]
        concepts = [row["name"] for row in term_rows if row["kind"] == "Concept"]
        topics = [row["name"] for row in term_rows if row["kind"] == "Topic"]
        return GraphExpansion(related_acts=related_acts, concepts=concepts, topics=topics)
