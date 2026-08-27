"""Интеграционные тесты инкрементального ingestion (бэклог п.1, задача «Корпус»).

Отдельная коллекция Qdrant (chunks_test) + изолированная БД graphrag_itg_*:
снапшот ingest_files живёт в тестовой БД, чужие данные не затрагиваются.
LM Studio не требуется (Concepts выключены). Запуск: make test-integration
"""

import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from app.config import Settings
from app.db.models import IngestFile
from app.ingestion.neo4j_writer import Neo4jWriter
from app.ingestion.pipeline import run_ingestion
from app.ingestion.qdrant_writer import QdrantWriter
from qdrant_client import AsyncQdrantClient
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.helpers import CREDENTIALS  # noqa: F401 - фиксация окружения

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ruslawod"
TEST_COLLECTION = "chunks_test"

# как в test_ingestion.py: акт со ссылкой на фикстурный 900000101
REF_ACT_XML = """<?xml version="1.0" ?><act><body><textIPS>
 ФЕДЕРАЛЬНЫЙ ЗАКОН
 О внесении изменений в Гражданский процессуальный кодекс
 Статья 1. Внести в &lt;ref nd=&quot;900000101&quot;&gt;Гражданский процессуальный кодекс РСФСР&lt;/ref&gt; изменения.
 Статья 2. Настоящий закон вступает в силу со дня официального опубликования.
</textIPS></body><meta><identification><pravogovruNd val="999000001"/><issuedByIPS val="Федеральный закон"/><doc_typeIPS val="Федеральный закон"/><doc_author_normal_formIPS val="Российская Федерация"/><docdateIPS val="15.03.2001"/><docNumberIPS val="22-ФЗ"/><headingIPS val="О внесении изменений в ГПК РСФСР"/><statusIPS val="Действует без изменений"/></identification><keywords><keywordsByIPS val="ГРАЖДАНСКИЙ ПРОЦЕСС"/></keywords><reference><classifierByIPS/></reference></meta></act>"""

# новый акт для сценария «докладывание файла»: ссылается на 900000101
NEW_ACT_XML = REF_ACT_XML.replace("999000001", "999000002").replace("22-ФЗ", "23-ФЗ").replace(
    "15.03.2001", "16.03.2001"
)

TEST_ACT_IDS = ("900000101", "900000102", "999000001", "999000002")


class StubEmbedder:
    """Детерминированный стаб эмбеддинга (как в test_ingestion.py)."""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    @property
    def loaded(self) -> bool:
        return True

    def encode(self, texts: list[str]) -> list[list[float]]:
        import hashlib

        vectors = []
        for text in texts:
            digest = hashlib.md5(text.encode("utf-8")).digest()  # noqa: S324 - стаб
            vector = [b / 255.0 for b in digest[: self.dim]]
            norm = sum(v * v for v in vector) ** 0.5 or 1.0
            vectors.append([v / norm for v in vector])
        return vectors


@pytest.fixture
async def corpus_dir(tmp_path: Path) -> Path:
    for name in ("act_full.xml", "act_empty_meta.xml"):
        shutil.copy(FIXTURES / name, tmp_path / name)
    (tmp_path / "act_ref.xml").write_text(REF_ACT_XML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def itg_settings(base_settings: Settings) -> Settings:
    return base_settings.model_copy(
        update={
            "qdrant_collection": TEST_COLLECTION,
            "ingest_extract_concepts": False,
            "chunk_max_chars": 400,
        }
    )


@pytest.fixture
async def session_factory(pg_dsn: str) -> AsyncIterator[async_sessionmaker]:
    """Фабрика сессий тестовой БД (миграции применены pg_dsn-фикстурой)."""
    engine = create_async_engine(pg_dsn, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(sa_delete(IngestFile))
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.fixture
async def qdrant(itg_settings: Settings) -> AsyncIterator[QdrantWriter]:
    client = AsyncQdrantClient(url=itg_settings.qdrant_url, timeout=30)
    writer = QdrantWriter(client, itg_settings.qdrant_collection)
    yield writer
    await client.delete_collection(TEST_COLLECTION)
    await client.close()


@pytest.fixture
async def neo4j(itg_settings: Settings) -> AsyncIterator[Neo4jWriter]:
    writer = Neo4jWriter(
        itg_settings.neo4j_uri,
        itg_settings.neo4j_user,
        itg_settings.neo4j_password.get_secret_value(),
    )
    await _purge_test_acts(writer)
    try:
        yield writer
    finally:
        await _purge_test_acts(writer)
        await writer.close()


async def _purge_test_acts(writer: Neo4jWriter) -> None:
    async with writer._driver.session() as session:
        await session.run(
            "MATCH (a:Act) WHERE a.id IN $ids DETACH DELETE a",
            ids=list(TEST_ACT_IDS),
        )


async def _scoped_act_count(neo4j: Neo4jWriter) -> int:
    async with neo4j._driver.session() as session:
        result = await session.run(
            "MATCH (a:Act) WHERE a.id IN $ids RETURN count(a) AS cnt",
            ids=list(TEST_ACT_IDS),
        )
        rows = await result.data()
    return int(rows[0]["cnt"])


async def _snapshot_names(factory: async_sessionmaker) -> set[str]:
    async with factory() as session:
        rows = (await session.execute(select(IngestFile))).scalars().all()
    return {row.filename for row in rows}


class TestIncrementalIngestion:
    async def test_full_scenario_add_change_remove(
        self,
        itg_settings: Settings,
        corpus_dir: Path,
        qdrant: QdrantWriter,
        neo4j: Neo4jWriter,
        session_factory: async_sessionmaker,
    ) -> None:
        # --- Прогон 1: всё новое ---
        first = await run_ingestion(
            itg_settings, corpus_dir, embedder=StubEmbedder(), session_factory=session_factory
        )
        assert first.files_added == 3
        assert first.files_skipped == 0
        assert first.chunks_written > 0
        assert await _snapshot_names(session_factory) == {
            "act_full.xml",
            "act_empty_meta.xml",
            "act_ref.xml",
        }
        assert await _scoped_act_count(neo4j) == 3

        # --- Прогон 2: без изменений - полный skip, эмбеддинги не считаются ---
        second = await run_ingestion(
            itg_settings, corpus_dir, embedder=StubEmbedder(), session_factory=session_factory
        )
        assert second.files_skipped == 3
        assert second.files_added == 0
        assert second.files_changed == 0
        assert second.chunks_written == 0, "unchanged-файлы не переэмбеддиваются"
        total_points = sum([await qdrant.count_act_points(a) for a in TEST_ACT_IDS[:3]])
        assert total_points == first.chunks_written, "точки Qdrant на месте"

        # --- Прогон 3: докладывание нового файла ---
        (corpus_dir / "act_new.xml").write_text(NEW_ACT_XML, encoding="utf-8")
        third = await run_ingestion(
            itg_settings, corpus_dir, embedder=StubEmbedder(), session_factory=session_factory
        )
        assert third.files_added == 1
        assert third.files_skipped == 3
        assert third.chunks_written > 0
        assert await _scoped_act_count(neo4j) == 4
        # REFERENCES нового акта на существующий из корпуса
        async with neo4j._driver.session() as session:
            rows = await (
                await session.run(
                    "MATCH (:Act {id: '999000002'})-[:REFERENCES]->(b:Act) RETURN b.id AS id"
                )
            ).data()
        assert rows[0]["id"] == "900000101"

        # --- Прогон 4: изменение файла (тот же act_id, другой текст) ---
        changed = NEW_ACT_XML.replace("999000002", "999000001").replace(
            "О внесении изменений в ГПК", "О внесении изменений в ГПК (в редакции 2026)"
        )
        (corpus_dir / "act_ref.xml").write_text(changed, encoding="utf-8")
        fourth = await run_ingestion(
            itg_settings, corpus_dir, embedder=StubEmbedder(), session_factory=session_factory
        )
        assert fourth.files_changed == 1
        assert fourth.files_skipped == 3
        assert fourth.chunks_written > 0, "изменённый файл переэмбеддится"
        # 3 старых акта + 999000002 из act_new.xml
        assert await _scoped_act_count(neo4j) == 4

        # --- Прогон 5: удаление файла -> зачистка актов ---
        (corpus_dir / "act_new.xml").unlink()
        fifth = await run_ingestion(
            itg_settings, corpus_dir, embedder=StubEmbedder(), session_factory=session_factory
        )
        assert fifth.files_removed == 1
        assert fifth.files_skipped == 3
        assert await qdrant.count_act_points("999000002") == 0
        assert await _scoped_act_count(neo4j) == 3

    async def test_full_flag_reprocesses_unchanged(
        self,
        itg_settings: Settings,
        corpus_dir: Path,
        qdrant: QdrantWriter,
        neo4j: Neo4jWriter,
        session_factory: async_sessionmaker,
    ) -> None:
        await run_ingestion(
            itg_settings, corpus_dir, embedder=StubEmbedder(), session_factory=session_factory
        )
        full = await run_ingestion(
            itg_settings,
            corpus_dir,
            embedder=StubEmbedder(),
            session_factory=session_factory,
            full=True,
        )
        assert full.files_changed == 3, "full игнорирует совпадающие хеши"
        assert full.chunks_written > 0
        assert full.files_skipped == 0
        # снапшот после полного прогона остаётся консистентным
        assert await _snapshot_names(session_factory) == {
            "act_full.xml",
            "act_empty_meta.xml",
            "act_ref.xml",
        }

    async def test_snapshot_persists_and_retry_broken_file(
        self,
        itg_settings: Settings,
        corpus_dir: Path,
        qdrant: QdrantWriter,
        neo4j: Neo4jWriter,
        session_factory: async_sessionmaker,
    ) -> None:
        # битый файл не попадает в снапшот и ретраится следующим прогоном
        (corpus_dir / "broken.xml").write_text("<not-a-valid-act>", encoding="utf-8")
        first = await run_ingestion(
            itg_settings, corpus_dir, embedder=StubEmbedder(), session_factory=session_factory
        )
        assert len(first.parse_errors) == 1
        assert "broken.xml" not in await _snapshot_names(session_factory)

        # чиним файл - следующий прогон обрабатывает только его
        (corpus_dir / "broken.xml").write_text(REF_ACT_XML, encoding="utf-8")
        second = await run_ingestion(
            itg_settings, corpus_dir, embedder=StubEmbedder(), session_factory=session_factory
        )
        assert second.files_added == 1
        assert second.files_skipped == 3
        assert second.parse_errors == []
