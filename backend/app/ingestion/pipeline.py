"""Оркестратор ingestion-конвейера: XML -> чанки -> векторы/понятия -> Qdrant + Neo4j.

Порядок (см. PLAN.md, этап 4 и dataflow):
1. Парсинг ВСЕХ XML корпуса (дёшево); сбор id для фильтра REFERENCES.
2. Инкрементальный план по sha256 (снапшот прошлого прогона в PG, таблица
   ingest_files): unchanged - только дешёвый граф-рефреш; changed/added -
   полный путь (chunk -> embed -> upsert -> concepts -> MERGE); removed -
   зачистка актов из Qdrant и Neo4j.
3. ensure_collection / ensure_schema.
4. Запись нового снапшота корпуса.

``full=True`` игнорирует снапшот и пересчитывает всё (смена чанкера/модели
эмбеддингов/процентов грифа). Без ``session_factory`` снапшот недоступен -
прогон идёт как полный (режим unit-тестов на стабах).
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from qdrant_client import AsyncQdrantClient
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.ingestion.chunker import chunk_act
from app.ingestion.cleaner import clean_text
from app.ingestion.clearance import resolve_clearance
from app.ingestion.concepts import ConceptExtractor
from app.ingestion.embeddings import Embedder, EmbeddingBackend
from app.ingestion.incremental import (
    FileSnapshot,
    IncrementalPlan,
    plan_incremental,
    scan_files,
    sha256_bytes,
)
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
    # инкрементальный режим (бэклог п.1)
    files_skipped: int = 0
    files_added: int = 0
    files_changed: int = 0
    files_removed: int = 0


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


async def _load_snapshot(
    session_factory: async_sessionmaker | None,
) -> dict[str, FileSnapshot]:
    """Снапшот корпуса прошлого прогона; без БД - пустой (полный прогон)."""
    if session_factory is None:
        logger.info("Снапшот корпуса недоступен (нет БД) - прогон идёт как полный")
        return {}
    from sqlalchemy import select

    from app.db.models import IngestFile

    async with session_factory() as session:
        rows = (await session.execute(select(IngestFile))).scalars().all()
    return {
        row.filename: FileSnapshot(
            sha256=row.sha256,
            act_ids=tuple(row.act_ids or []),
            chunks_count=row.chunks_count,
        )
        for row in rows
    }


async def _save_snapshot(
    session_factory: async_sessionmaker | None,
    records: dict[str, FileSnapshot],
) -> None:
    """Перезаписать снапшот корпуса текущим состоянием (таблица мала - целиком)."""
    if session_factory is None:
        return
    from app.db.models import IngestFile

    async with session_factory() as session:
        await session.execute(sa_delete(IngestFile))
        session.add_all(
            IngestFile(
                filename=name,
                sha256=snap.sha256,
                act_ids=list(snap.act_ids),
                chunks_count=snap.chunks_count,
            )
            for name, snap in records.items()
        )
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
    full: bool = False,
) -> IngestStats:
    """Прогон ingestion по каталогу XML-файлов (по умолчанию инкрементальный).

    ``embedder`` - точка расширения для тестов (стаб с детерминированными
    векторами); по умолчанию создаётся реальный bge-m3 (ADR-009).
    ``progress`` - необязательный объект живого прогресса для API-статуса.
    ``run_id`` / ``session_factory`` - персистентность прогона и снапшота корпуса.
    ``full=True`` - форс-полный прогон (игнорировать снапшот).
    """
    stats = IngestStats()
    do_concepts = (
        settings.ingest_extract_concepts if extract_concepts is None else extract_concepts
    )

    xml_files = scan_files(corpus_dir)
    stats.files_total = len(xml_files)
    if not xml_files:
        logger.warning("В каталоге %s нет XML-файлов", corpus_dir)
        return stats

    if progress is not None:
        progress.stage, progress.files_done, progress.files_total = "parse", 0, len(xml_files)
    await _persist(session_factory, run_id, stage="parse", files_done=0, files_total=len(xml_files))
    logger.info(
        "Ingestion запущен: файлов=%d, concepts=%s, корпус=%s, full=%s",
        len(xml_files),
        do_concepts,
        corpus_dir,
        full,
    )

    acts: list[ParsedAct] = []
    parsed_by_file: dict[str, list[ParsedAct]] = {}
    current_hashes: dict[str, str] = {}
    for path in xml_files:
        try:
            raw = path.read_bytes()
            act = parse_act(raw)
            acts.append(act)
            parsed_by_file.setdefault(path.name, []).append(act)
            current_hashes[path.name] = sha256_bytes(raw)
        except Exception as exc:  # битые файлы не останавливают прогон
            stats.parse_errors.append(f"{path.name}: {exc}")
            logger.warning("Файл не разобран: %s", exc)
    stats.acts_parsed = len(acts)
    corpus_ids = {act.id for act in acts}

    previous = await _load_snapshot(session_factory)
    plan: IncrementalPlan = plan_incremental(previous, current_hashes, full=full)
    stats.files_skipped = len(plan.unchanged)
    stats.files_added = len(plan.added)
    stats.files_changed = len(plan.changed)
    stats.files_removed = len(plan.removed)
    logger.info(
        "Инкрементальный план: новых=%d, изменённых=%d, без изменений=%d, удалено=%d",
        stats.files_added,
        stats.files_changed,
        stats.files_skipped,
        stats.files_removed,
    )

    # Зачистка: файлы, исчезнувшие из каталога + изменившиеся файлы, которые
    # больше не парсятся (старые акты - в мусор по act_ids из снапшота)
    stale_act_ids: list[str] = []
    for name in plan.removed:
        stale_act_ids.extend(previous[name].act_ids)
    for name in plan.to_process:
        if not parsed_by_file.get(name) and name in previous:
            stale_act_ids.extend(previous[name].act_ids)

    if progress is not None:
        progress.files_done = stats.files_skipped + len(stats.parse_errors)
    await _persist(
        session_factory,
        run_id,
        files_done=stats.files_skipped + len(stats.parse_errors),
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
        # Тяжёлая модель нужна только для файлов с полным путём обработки;
        # загрузка (torch + веса) занимает десятки секунд - только в потоке,
        # иначе блокируем event loop и API перестаёт отвечать
        if plan.to_process:
            if progress is not None:
                progress.stage = "model"
            await _persist(session_factory, run_id, stage="model")
            logger.info("Загрузка модели эмбеддингов (%s)...", settings.embedding_model)
            dim = await asyncio.to_thread(lambda: embedder.dim)
            logger.info("Модель готова (dim=%d), проверка коллекции/схемы", dim)
            await qdrant.ensure_collection(dim)
            await neo4j.ensure_schema()
        else:
            await neo4j.ensure_schema()

        # 1. Зачистка удалённых/сломанных (дешёвые delete, без модели)
        for act_id in stale_act_ids:
            await qdrant.delete_act(act_id)
            await neo4j.delete_act(act_id)
        if stale_act_ids:
            logger.info("Зачищено актов удалённых/сломанных файлов: %d", len(stale_act_ids))

        # 2. Дешёвый граф-рефреш unchanged-файлов: детерминированная часть без
        # эмбеддингов/LLM - старые акты получают REFERENCES на новые
        for name in plan.unchanged:
            for act in parsed_by_file.get(name, []):
                await neo4j.upsert_act(
                    act,
                    clearance=resolve_clearance(
                        act.id,
                        internal_percent=settings.ingest_internal_percent,
                        secret_percent=settings.ingest_secret_percent,
                    ),
                    topics=list(topics_of(act)),
                    ref_ids=list(filter_references(act, corpus_ids)),
                )
        if plan.unchanged:
            logger.info("Граф-рефреш без изменений: файлов=%d", len(plan.unchanged))

        # 3. Полный путь обработки только для дельты
        extractor = ConceptExtractor(
            LLMClient(_llm_config(settings)),
            max_per_chunk=settings.ingest_concept_max_per_chunk,
        )
        semaphore = asyncio.Semaphore(_CONCURRENCY)
        records: dict[str, FileSnapshot] = {
            name: previous[name] for name in plan.unchanged if name in previous
        }

        total_acts = sum(len(parsed_by_file.get(name, [])) for name in plan.to_process)
        processed_acts = 0
        next_milestone = 1  # очередная граница ~10% для INFO-лога прогресса
        if progress is not None:
            progress.stage = "processing"
        await _persist(session_factory, run_id, stage="processing")

        for name in plan.to_process:
            file_acts = parsed_by_file.get(name, [])
            file_chunks = 0
            for act in file_acts:
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

                # encode блокирует GIL надолго (torch/CPU) - только в отдельном потоке
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
                file_chunks += len(chunks)

                concepts: list[str] = []
                if do_concepts and chunks:
                    results = await asyncio.gather(
                        *(_extract_with_semaphore(semaphore, extractor, c.text) for c in chunks)
                    )
                    seen: set[str] = set()
                    for names in results:
                        for cname in names:
                            key = cname.casefold()
                            if key not in seen:
                                seen.add(key)
                                concepts.append(cname)
                    stats.concepts_extracted += len(concepts)

                await neo4j.upsert_act(
                    act,
                    clearance=clearance,
                    topics=list(topics_of(act)),
                    ref_ids=list(filter_references(act, corpus_ids)),
                    concepts=concepts,
                )
                processed_acts += 1
                logger.debug(
                    "Акт %s (%s/%d): чанков=%d, clearance=%s, concepts=%d",
                    act.id,
                    name,
                    total_acts,
                    len(chunks),
                    clearance,
                    len(concepts),
                )
            records[name] = FileSnapshot(
                sha256=current_hashes[name],
                act_ids=tuple(a.id for a in file_acts),
                chunks_count=file_chunks,
            )
            if progress is not None:
                progress.files_done = (
                    stats.files_skipped + len(stats.parse_errors) + processed_acts
                )
                progress.chunks_done = stats.chunks_written
            await _persist(
                session_factory,
                run_id,
                files_done=stats.files_skipped + len(stats.parse_errors) + processed_acts,
                chunks_done=stats.chunks_written,
            )
            percent = processed_acts * 100 // total_acts if total_acts else 100
            if percent >= next_milestone * 10 or name == plan.to_process[-1]:
                logger.info(
                    "Прогресс %d%%: актов %d/%d, чанков всего=%d",
                    min(percent, 100),
                    processed_acts,
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

    await _save_snapshot(session_factory, records)

    logger.info(
        "Ingestion завершён: файлов=%d (новых=%d, изменённых=%d, пропущено=%d, удалено=%d), "
        "актов=%d, чанков=%d, concepts=%d, ошибок парсинга=%d",
        stats.files_total,
        stats.files_added,
        stats.files_changed,
        stats.files_skipped,
        stats.files_removed,
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
