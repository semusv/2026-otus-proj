"""Unit-тесты эмбеддинг-обёртки и LLM-экстракции Concept (LLM - мок)."""

import pytest
from app.config import Settings
from app.ingestion.concepts import ConceptExtractor
from app.ingestion.embeddings import Embedder
from app.llm.client import LLMClient, LLMConfig, parse_loose_json

pytestmark = pytest.mark.unit


class TestParseLooseJson:
    def test_clean_json(self) -> None:
        assert parse_loose_json('["a", "b"]') == ["a", "b"]

    def test_code_fence(self) -> None:
        assert parse_loose_json('```json\n["x"]\n```') == ["x"]

    def test_json_with_prose_around(self) -> None:
        assert parse_loose_json('Вот понятия: ["ипотека", "залог"] - готово.') == [
            "ипотека",
            "залог",
        ]

    def test_garbage_returns_none(self) -> None:
        assert parse_loose_json("никакого json тут нет") is None


class FakeLLM(LLMClient):
    """Мок LLMClient: возвращает заготовленный ответ / кидает исключение."""

    def __init__(
        self,
        settings: Settings,
        result: object = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__(LLMConfig(settings.llm_base_url, "key", settings.llm_model))
        self.result = result
        self.error = error
        self.calls: list[str] = []

    async def complete_json(self, system: str, user: str, **kwargs: float) -> object:
        self.calls.append(user)
        if self.error:
            raise self.error
        return self.result


class TestConceptExtractor:
    async def test_extracts_unique_normalized(self, settings: Settings) -> None:
        llm = FakeLLM(
            settings,
            result=["Доверенность", "  доверенность  ", "ипотека.", 42, ""],
        )
        extractor = ConceptExtractor(llm, max_per_chunk=6)
        assert await extractor.extract("текст чанка") == ["Доверенность", "ипотека"]

    async def test_respects_max_per_chunk(self, settings: Settings) -> None:
        llm = FakeLLM(settings, result=[f"понятие-{i}" for i in range(10)])
        extractor = ConceptExtractor(llm, max_per_chunk=3)
        assert len(await extractor.extract("текст")) == 3

    async def test_llm_failure_yields_empty_list(self, settings: Settings) -> None:
        llm = FakeLLM(settings, error=RuntimeError("LM Studio недоступен"))
        extractor = ConceptExtractor(llm, max_per_chunk=6)
        assert await extractor.extract("текст") == []

    async def test_non_list_answer_yields_empty(self, settings: Settings) -> None:
        llm = FakeLLM(settings, result={"не": "массив"})
        extractor = ConceptExtractor(llm, max_per_chunk=6)
        assert await extractor.extract("текст") == []

    async def test_chunk_text_passed_to_llm_truncated(self, settings: Settings) -> None:
        llm = FakeLLM(settings, result=["a"])
        extractor = ConceptExtractor(llm, max_per_chunk=6)
        await extractor.extract("х" * 10_000)
        assert len(llm.calls[0]) <= 6000


class TestEmbedderLazyLoad:
    def test_not_loaded_on_init(self) -> None:
        embedder = Embedder("BAAI/bge-m3", batch_size=4)
        assert embedder.loaded is False

    def test_encode_empty_list_no_model_load(self) -> None:
        embedder = Embedder("BAAI/bge-m3")
        assert embedder.encode([]) == []
        assert embedder.loaded is False


class TestLLMClientWiring:
    def test_client_builds_with_config(self) -> None:
        client = LLMClient(LLMConfig("http://127.0.0.1:1234/v1", "key", "model-x"))
        assert client.model == "model-x"
