"""Reranker кандидатов контекста: bge-reranker-v2-m3 через CrossEncoder (ADR-009).

Модель загружается лениво при первом вызове; предсказание - блокирующая CPU-операция,
поэтому наружу отдаётся только async-API, внутри - asyncio.to_thread.
"""

import asyncio
from typing import Any

from app.rag.retrievers import RetrievedChunk


class Reranker:
    """Пересортировка кандидатов по парной релевантности (query, text)."""

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
            # импорт внутри метода: torch поднимается только при реальном запросе чата
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name, device=self._device)
        return self._model

    def _score_sync(self, query: str, texts: list[str]) -> list[float]:
        model = self._ensure_model()
        pairs = [(query, text) for text in texts]
        raw = model.predict(pairs, batch_size=self._batch_size, show_progress_bar=False)
        return [float(score) for score in raw]

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        *,
        top_n: int,
    ) -> list[RetrievedChunk]:
        """Возвращает топ-N кандидатов с пересчитанным score реранкера."""
        if not candidates:
            return []
        scores = await asyncio.to_thread(
            self._score_sync, query, [chunk.text for chunk in candidates]
        )
        rescored = [
            RetrievedChunk(
                act_id=chunk.act_id,
                title=chunk.title,
                chunk_no=chunk.chunk_no,
                clearance=chunk.clearance,
                text=chunk.text,
                score=score,
            )
            for chunk, score in zip(candidates, scores, strict=True)
        ]
        rescored.sort(key=lambda item: item.score, reverse=True)
        return rescored[:top_n]
