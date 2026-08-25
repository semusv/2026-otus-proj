"""LLM-экстракция юридических понятий (:Concept) из текста чанков.

Нейро-символическая часть GraphRAG (ADR-006): детерминированные рёбра строятся
из метаданных XML, а MENTIONS->Concept добывается LLM из содержимого чанков.
Любая ошибка LLM трактуется как «понятий не извлечено» - ingestion не падает.
"""

import logging

from app.llm.client import LLMClient

logger = logging.getLogger("app.ingestion.concepts")

_SYSTEM_PROMPT = (
    "Ты - ассистент по индексации российских правовых актов. "
    "Из текста документа выдай список ключевых юридических понятий и терминов "
    "(например: 'доверенность', 'ипотека', 'кооператив'), релевантных для поиска. "
    "Отвечай ТОЛЬКО JSON-массивом строк, без пояснений, максимум {max} элементов."
)


class ConceptExtractor:
    """Извлекает понятия из одного чанка; устойчив к сбоям LLM."""

    def __init__(self, llm: LLMClient, *, max_per_chunk: int = 6) -> None:
        self._llm = llm
        self._max_per_chunk = max_per_chunk

    async def extract(self, text: str) -> list[str]:
        """Возвращает уникальные понятия; при любой ошибке - пустой список."""
        try:
            result = await self._llm.complete_json(
                _SYSTEM_PROMPT.format(max=self._max_per_chunk),
                text[:6000],
                temperature=0.1,
                max_tokens=256,
            )
        except Exception:  # except Exception: сбои LLM не должны ломать ingestion
            logger.warning("LLM-экстракция Concept не удалась", exc_info=True)
            return []
        if not isinstance(result, list):
            return []
        concepts: list[str] = []
        for item in result:
            if not isinstance(item, str):
                continue
            name = " ".join(item.split()).strip(" .")
            if name and name.casefold() not in {c.casefold() for c in concepts}:
                concepts.append(name)
        return concepts[: self._max_per_chunk]
