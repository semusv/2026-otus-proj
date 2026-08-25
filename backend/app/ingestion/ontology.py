"""Маппинг ParsedAct на онтологию графа (ADR-006): чистые функции без Neo4j.

Онтология (4 типа узлов):
    (:Act {id, title, doc_number, date, status, clearance})
    (:Authority {name}), (:Topic {name}), (:Concept {name} - LLM-экстракция)
Рёбра: ISSUED_BY, REFERENCES (только на акты из корпуса), HAS_TOPIC, MENTIONS.
"""

from typing import Any

from app.ingestion.clearance import Clearance
from app.ingestion.parser import ParsedAct


def act_properties(act: ParsedAct, clearance: Clearance) -> dict[str, Any]:
    """Свойства узла (:Act); None-поля сохраняются для явности схемы."""
    return {
        "id": act.id,
        "title": act.title,
        "doc_number": act.doc_number or None,
        "date": act.date,
        "status": act.status or None,
        "doc_type": act.doc_type or None,
        "keywords": list(act.keywords),
        "clearance": clearance,
    }


def filter_references(act: ParsedAct, corpus_ids: set[str]) -> tuple[str, ...]:
    """REFERENCES создаётся только если целевой акт есть в корпусе."""
    return tuple(ref for ref in act.ref_ids if ref in corpus_ids)


def topics_of(act: ParsedAct) -> tuple[str, ...]:
    """Темы = названия классификатора + ключевые слова (без дублей, порядок сохранён)."""
    topics: list[str] = []
    for candidate in (*act.topics, *act.keywords):
        name = candidate.strip()
        key = name.casefold()
        if key not in {t.casefold() for t in topics}:
            topics.append(name)
    return tuple(topics)
