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
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.ingestion.chunker import chunk_act
from app.ingestion.cleaner import clean_text
from app.ingestion.clearance import resolve_clearance
from app.ingestion.concepts import ConceptExtractor
from app.ingestion.embeddings import Embedder, EmbeddingBackend
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


@dataclass
class IngestProgress:
    """Живой прогресс прогона - читается /admin/ingest/status во время работы.

    Крупные вехи (бэклог этапа 10): не мельчим - стадии и каждые ~10% файлов.
    """

    stage: str = "parse"  # parse -> model -> processing -> done/error
    files_done: int = 0
    files_total: int = 0
    chunks_done: int = 0


async def _persist(
    session_factory: async_sessionmaker | None,
    run_id: uuid.UUID | None,
    **fields: object,
) -> None:
    """Записать прогресс/статус в ingestion_runs (если переданы run_id + session_factory)."""
    if session_factory is None or run_id is None:
        return
    from sqlalchemy import update

    from app.db.models import IngestionRun

    async with session_factory() as session:
        stmt = update(IngestionRun).where(IngestionRun.id == run_id).values(**fields)
        await session.execute(stmt)
        await session.commit()


async def run_ingestion(
    settings: Settings,
    corpus_dir: Path,
    *,
    extract_concepts: bool | None = None,
    embedder: EmbeddingBackend | None = None,
    progress: IngestProgress | None = None,
    run_id: uuid.UUID | None = None,
    session_factory: async_sessionmaker | None = None,
) -> IngestStats:
    """Полный прогон ingestion по каталогу XML-файлов.

    ``embedder`` - точка расширения для тестов (стаб с детерминированными
    векторами); по умолчанию создаётся реальный bge-m3 (ADR-009).
    ``progress`` - необязательный объект живого прогресса для API-статуса.
    ``run_id`` / ``session_factory`` - опциональная персистентность в ingestion_runs.
    """
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

    if progress is not None:
        progress.stage, progress.files_done, progress.files_total = "parse", 0, len(xml_files)
    await _persist(session_factory, run_id, stage="parse", files_done=0, files_total=len(xml_files))
    logger.info(
        "Ingestion запущен: файлов=%d, concepts=%s, корпус=%s",
        len(xml_files),
        do_concepts,
        corpus_dir,
    )

    acts: list[ParsedAct] = []
    for path in xml_files:
        try:
            acts.append(parse_act(path.read_bytes()))
        except Exception as exc:  # битые файлы не останавливают прогон
            stats.parse_errors.append(f"{path.name}: {exc}")
            logger.warning("Файл не разобран: %s", exc)
    stats.acts_parsed = len(acts)
    corpus_ids = {act.id for act in acts}
    if progress is not None:
        progress.files_done = stats.acts_parsed + len(stats.parse_errors)
    await _persist(session_factory, run_id, files_done=stats.acts_parsed + len(stats.parse_errors))
    logger.info(
        "Парсинг завершён: актов=%d, ошибок разбора=%d",
        stats.acts_parsed,
        len(stats.parse_errors),
    )

    embedder = embedder or Embedder(
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
        # первая загрузка модели (torch + веса с диска/HF) занимает десятки секунд -
        # только в потоке, иначе блокируем event loop и API перестаёт отвечать
        if progress is not None:
            progress.stage = "model"
        await _persist(session_factory, run_id, stage="model")
        logger.info("Загрузка модели эмбеддингов (%s)...", settings.embedding_model)
        dim = await asyncio.to_thread(lambda: embedder.dim)
        logger.info("Модель готова (dim=%d), проверка коллекции/схемы", dim)
        await qdrant.ensure_collection(dim)
        await neo4j.ensure_schema()

        extractor = ConceptExtractor(
            LLMClient(_llm_config(settings)),
            max_per_chunk=settings.ingest_concept_max_per_chunk,
        )
        semaphore = asyncio.Semaphore(_CONCURRENCY)

        total_acts = len(acts)
        next_milestone = 1  # очередная граница ~10% для INFO-лога прогресса
        for idx, act in enumerate(acts, start=1):
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

            # encode блокирует GIL надолго (torch/CPU) - только в отдельном потоке,
            # иначе event loop стоит и API перестаёт отвечать на время прогона
            vectors = await asyncio.to_thread(embedder.encode, [c.text for c in chunks])
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
            logger.debug(
                "Акт %s (%d/%d): чанков=%d, clearance=%s, concepts=%d",
                act.id,
                idx,
                total_acts,
                len(chunks),
                clearance,
                len(concepts),
            )
            if progress is not None:
                progress.stage = "processing"
                progress.files_done = idx
                progress.chunks_done = stats.chunks_written
            percent = idx * 100 // total_acts
            if percent >= next_milestone * 10 or idx == total_acts:
                await _persist(
                    session_factory, run_id,
                    stage="processing", files_done=idx, chunks_done=stats.chunks_written,
                )
            if percent >= next_milestone * 10 or idx == total_acts:
                logger.info(
                    "Прогресс %d%%: актов %d/%d, чанков всего=%d",
                    min(percent, 100),
                    idx,
                    total_acts,
                    stats.chunks_written,
                )
                next_milestone += 1
    finally:
        await qdrant_client.close()
        await neo4j.close()
        if progress is not None:
            progress.stage = "done"
        await _persist(session_factory, run_id, stage="done")

    logger.info(
        "Ingestion завершён: актов=%d, чанков=%d, concepts=%d, ошибок парсинга=%d",
        stats.acts_parsed,
        stats.chunks_written,
        stats.concepts_extracted,
        len(stats.parse_errors),
    )
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
