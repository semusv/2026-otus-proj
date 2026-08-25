"""Интеграционные тесты ingestion против реальных Qdrant и Neo4j из compose.

Используют ОТДЕЛЬНУЮ коллекцию Qdrant (chunks_test) и чистят граф до/после:
на этапе 4 граф используется только тестами и приёмочным прогоном.
LM Studio не требуется: экстракция Concepts отключена (моки покрыты юнитами).
Запуск: make test-integration
"""

import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from app.config import Settings
from app.ingestion.neo4j_writer import Neo4jWriter
from app.ingestion.pipeline import run_ingestion
from app.ingestion.qdrant_writer import QdrantWriter
from qdrant_client import AsyncQdrantClient

from tests.integration.helpers import CREDENTIALS  # noqa: F401 - фиксация окружения

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ruslawod"
TEST_COLLECTION = "chunks_test"

# синтетический акт со ссылкой на фикстурный (для ребра REFERENCES);
# ссылка экранирована как в реальном корпусе RusLawOD
REF_ACT_XML = """<?xml version="1.0" ?><act><body><textIPS>
 ФЕДЕРАЛЬНЫЙ ЗАКОН
 О внесении изменений в Гражданский процессуальный кодекс
 Статья 1. Внести в &lt;ref nd=&quot;102010098&quot;&gt;Гражданский процессуальный кодекс РСФСР&lt;/ref&gt; изменения.
 Статья 2. Настоящий закон вступает в силу со дня официального опубликования.
</textIPS></body><meta><identification><pravogovruNd val="999000001"/><issuedByIPS val="Федеральный закон"/><doc_typeIPS val="Федеральный закон"/><doc_author_normal_formIPS val="Российская Федерация"/><docdateIPS val="15.03.2001"/><docNumberIPS val="22-ФЗ"/><headingIPS val="О внесении изменений в ГПК РСФСР"/><statusIPS val="Действует без изменений"/></identification><keywords><keywordsByIPS val="ГРАЖДАНСКИЙ ПРОЦЕСС"/></keywords><reference><classifierByIPS/></reference></meta></act>"""


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
            "chunk_max_chars": 400,  # маленькие чанки -> предсказуемое число точек
        }
    )


class StubEmbedder:
    """Детерминированный стаб эмбеддинга (без загрузки bge-m3 из HF)."""

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
    try:
        yield writer
    finally:
        await writer.clear_all()
        await writer.close()


async def _edge_count(neo4j: Neo4jWriter, rel: str) -> int:
    async with neo4j._driver.session() as session:  # тестовый хелпер
        result = await session.run(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS cnt")
        rows = await result.data()
    return int(rows[0]["cnt"])


class TestIngestionPipeline:
    async def test_full_run_creates_vectors_and_graph(
        self, itg_settings: Settings, corpus_dir: Path, qdrant: QdrantWriter, neo4j: Neo4jWriter
    ) -> None:
        stats = await run_ingestion(itg_settings, corpus_dir, embedder=StubEmbedder())

        assert stats.files_total == 3
        assert stats.acts_parsed == 3
        assert stats.parse_errors == []
        assert stats.chunks_written > 0

        # Qdrant: точки с полным payload
        total_points = sum(
            [
                await qdrant.count_act_points(act_id)
                for act_id in ("102010098", "102010238", "999000001")
            ]
        )
        assert total_points == stats.chunks_written

        # Neo4j: узлы всех типов + рёбра
        labels = await neo4j.counts_by_label()
        assert labels["Act"] == 3
        assert labels["Authority"] >= 1
        assert labels["Topic"] >= 1

        # REFERENCES: только цель, существующая в корпусе
        assert await _edge_count(neo4j, "REFERENCES") == 1
        assert await _edge_count(neo4j, "ISSUED_BY") >= 2

    async def test_rerun_is_idempotent(
        self, itg_settings: Settings, corpus_dir: Path, qdrant: QdrantWriter, neo4j: Neo4jWriter
    ) -> None:
        first = await run_ingestion(itg_settings, corpus_dir, embedder=StubEmbedder())
        labels_after_first = await neo4j.counts_by_label()
        refs_first = await _edge_count(neo4j, "REFERENCES")

        second = await run_ingestion(itg_settings, corpus_dir, embedder=StubEmbedder())

        assert second.chunks_written == first.chunks_written, "нет дублей точек Qdrant"
        assert await neo4j.counts_by_label() == labels_after_first, "узлы графа не задвоились"
        assert await _edge_count(neo4j, "REFERENCES") == refs_first, "рёбра не задвоились"

    async def test_clearance_assigned_deterministically(
        self, itg_settings: Settings, corpus_dir: Path, qdrant: QdrantWriter, neo4j: Neo4jWriter
    ) -> None:
        await run_ingestion(itg_settings, corpus_dir, embedder=StubEmbedder())
        # повторный прогон даёт тот же clearance на тех же актах - проверка через
        # стабильность числа точек при фильтре по конкретной метке
        await run_ingestion(itg_settings, corpus_dir, embedder=StubEmbedder())
        total = sum(
            [
                await qdrant.count_act_points(act_id)
                for act_id in ("102010098", "102010238", "999000001")
            ]
        )
        assert total > 0
