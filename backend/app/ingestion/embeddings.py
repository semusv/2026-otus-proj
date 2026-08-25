"""Эмбеддинги чанков: bge-m3 через sentence-transformers (ADR-009, CPU-in-process).

Модель загружается ЛЕНИВО при первом encode: импорт модуля не тянет torch,
приложение стартует быстро, а тяжёлая загрузка нужна только ingestion-пайплайну.
"""

from typing import Any, Protocol


class EmbeddingBackend(Protocol):
    """Контракт эмбеддера: точка подмены стабом в тестах."""

    @property
    def dim(self) -> int: ...

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class Embedder:
    """Обёртка SentenceTransformer с батч-кодированием и нормализацией векторов."""

    def __init__(self, model_name: str, *, batch_size: int = 32, device: str = "cpu") -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._device = device
        self._model: Any | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _ensure_model(self) -> Any:
        if self._model is None:
            # импорт внутри метода: torch поднимается только когда реально нужен
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name, device=self._device)
        return self._model

    @property
    def dim(self) -> int:
        model = self._ensure_model()
        return int(model.get_sentence_embedding_dimension())

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Батч-кодирование; L2-нормализация (косинусная близость == dot product)."""
        if not texts:
            return []
        model = self._ensure_model()
        embeddings = model.encode(
            texts,
            batch_size=self._batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [list(map(float, vector)) for vector in embeddings]
