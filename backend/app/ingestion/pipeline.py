"""Оркестратор ingestion-конвейера: XML -> чанки -> векторы/понятия -> Qdrant + Neo4j.

Порядок (см. PLAN.md, этап 4 и dataflow):
1. Парсинг всех XML корпуса; сбор id для фильтра REFERENCES.
2. ensure_collection / ensure_schema.
3. Для каждого акта: clean -> chunk -> embed -> upsert Qdrant (с предварительным
   delete по act_id - чистый ре-ingest) -> экстракция Concepts (опционально) ->
   MERGE в Neo4j.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from app.config import Settings
from app.ingestion.chunker import chunk_act
from app.ingestion.cleaner import clean_text
from app.ingestion.clearance import resolve_clearance
from app.ingestion.concepts import ConceptExtractor
from app.ingestion.embeddings import Embedder
from app.ingestion.neo4j_writer import Neo4jWriter
from app.ingestion.ontology import filter_references, topics_of
from app.ingestion.parser import ParsedAct, parse_act
from app.ingestion.qdrant_writer import QdrantWriter, VectorChunk
from app.llm.client import LLMClient, LLMConfig

logger = logging.getLogger("app.ingestion.pipeline")

# параллельные LLM-вызовы экстракции понятий
_CONCURRENCY = 4


@dataclass
class IngestStats:
    files_total: int = 0
    acts_parsed: int = 0
    parse_errors: list[str] = field(default_factory=list)
    chunks_written: int = 0
    concepts_extracted: int = 0


async def run_ingestion(
    settings: Settings,
    corpus_dir: Path,
    *,
    extract_concepts: bool | None = None,
) -> IngestStats:
    """Полный прогон ingestion по каталогу XML-файлов."""
    stats = IngestStats()
    do_concepts = (
        settings.ingest_extract_concepts if extract_concepts is None else extract_concepts
    )

    # ASYNC240: каталог локальный, листинг мгновенный; anyio.Path избыточен
    xml_files = sorted(corpus_dir.glob("*.xml"))  # noqa: ASYNC240
    stats.files_total = len(xml_files)
    if not xml_files:
        logger.warning("В каталоге %s нет XML-файлов", corpus_dir)
        return stats

    acts: list[ParsedAct] = []
    for path in xml_files:
        try:
            acts.append(parse_act(path.read_bytes()))
        except Exception as exc:  # битые файлы не останавливают прогон
            stats.parse_errors.append(f"{path.name}: {exc}")
    stats.acts_parsed = len(acts)
    corpus_ids = {act.id for act in acts}

    embedder = Embedder(
        settings.embedding_model,
        batch_size=settings.embedding_batch_size,
        device=settings.embedding_device,
    )
    qdrant_client = AsyncQdrantClient(url=settings.qdrant_url, timeout=60)
    qdrant = QdrantWriter(qdrant_client, settings.qdrant_collection)
    neo4j = Neo4jWriter(
        settings.neo4j_uri,
        settings.neo4j_user,
        settings.neo4j_password.get_secret_value(),
    )

    try:
        await qdrant.ensure_collection(embedder.dim)
        await neo4j.ensure_schema()

        extractor = ConceptExtractor(
            LLMClient(_llm_config(settings)),
            max_per_chunk=settings.ingest_concept_max_per_chunk,
        )
        semaphore = asyncio.Semaphore(_CONCURRENCY)

        for act in acts:
            clearance = resolve_clearance(
                act.id,
                internal_percent=settings.ingest_internal_percent,
                secret_percent=settings.ingest_secret_percent,
            )
            chunks = chunk_act(
                clean_text(act.text),
                max_chars=settings.chunk_max_chars,
                overlap_chars=settings.chunk_overlap_chars,
            )

            vectors = embedder.encode([c.text for c in chunks])
            await qdrant.delete_act(act.id)
            stats.chunks_written += await qdrant.upsert_chunks(
                [
                    VectorChunk(
                        chunk=c,
                        vector=v,
                        act_id=act.id,
                        act_title=act.title,
                        clearance=clearance,
                    )
                    for c, v in zip(chunks, vectors, strict=True)
                ]
            )

            concepts: list[str] = []
            if do_concepts and chunks:
                results = await asyncio.gather(
                    *(_extract_with_semaphore(semaphore, extractor, c.text) for c in chunks)
                )
                seen: set[str] = set()
                for names in results:
                    for name in names:
                        key = name.casefold()
                        if key not in seen:
                            seen.add(key)
                            concepts.append(name)
                stats.concepts_extracted += len(concepts)

            await neo4j.upsert_act(
                act,
                clearance=clearance,
                topics=list(topics_of(act)),
                ref_ids=list(filter_references(act, corpus_ids)),
                concepts=concepts,
            )
            logger.info(
                "Акт %s: чанков=%d, clearance=%s, concepts=%d",
                act.id,
                len(chunks),
                clearance,
                len(concepts),
            )
    finally:
        await qdrant_client.close()
        await neo4j.close()

    return stats


async def _extract_with_semaphore(
    semaphore: asyncio.Semaphore, extractor: ConceptExtractor, text: str
) -> list[str]:
    async with semaphore:
        return await extractor.extract(text)


def _llm_config(settings: Settings) -> LLMConfig:
    return LLMConfig(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
    )
