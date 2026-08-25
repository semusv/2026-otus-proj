"""Запись чанков с векторами в Qdrant.

Идемпотентность: id точки - UUIDv5 от (act_id, chunk_no), поэтому повторный
прогон ingestion обновляет те же точки, не создавая дублей.
"""

from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient, models

from app.ingestion.chunker import Chunk


def point_uuid(act_id: str, chunk_no: int) -> str:
    """Детерминированный UUID точки вектора."""
    return str(uuid5(NAMESPACE_URL, f"graphrag-chunk:{act_id}:{chunk_no}"))


@dataclass(frozen=True)
class VectorChunk:
    """Чанк вместе с эмбеддингом и меткой доступа акта."""

    chunk: Chunk
    vector: list[float]
    act_id: str
    act_title: str
    clearance: str


class QdrantWriter:
    """Управляет коллекцией ``chunks`` и батч-upsert'ом точек."""

    def __init__(self, client: AsyncQdrantClient, collection: str) -> None:
        self._client = client
        self._collection = collection

    async def ensure_collection(self, dim: int) -> None:
        """Создаёт коллекцию и payload-индекс по clearance, если их ещё нет."""
        if not await self._client.collection_exists(self._collection):
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=models.VectorParams(
                    size=dim, distance=models.Distance.COSINE
                ),
            )
        await self._client.create_payload_index(
            collection_name=self._collection,
            field_name="clearance",
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=True,
        )

    async def upsert_chunks(self, chunks: list[VectorChunk]) -> int:
        """Батч-upsert; возвращает число записанных точек."""
        if not chunks:
            return 0
        points = [
            models.PointStruct(
                id=point_uuid(item.act_id, item.chunk.chunk_no),
                vector=item.vector,
                payload={
                    "act_id": item.act_id,
                    "title": item.act_title,
                    "chunk_no": item.chunk.chunk_no,
                    "clearance": item.clearance,
                    "text": item.chunk.text,
                },
            )
            for item in chunks
        ]
        await self._client.upsert(collection_name=self._collection, points=points, wait=True)
        return len(points)

    async def count_act_points(self, act_id: str) -> int:
        """Число точек акта (для тестов идемпотентности и статуса прогона)."""
        result = await self._client.count(
            collection_name=self._collection,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="act_id", match=models.MatchValue(value=act_id)
                    )
                ]
            ),
            exact=True,
        )
        return result.count
