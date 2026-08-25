"""Planner агента: выбор инструментов и переписывание запроса.

Гибридная схема (решение этапа 5):
- первый проход - всегда оба инструмента (vec + graph), запрос без изменений;
- re-plan (после evaluate) - LLM возвращает JSON {tools, query}: какие инструменты
  добрать и уточнённый поисковый запрос; фолбэк при сбое LLM - правила
  (добавить недостающий инструмент / взять refined_query от evaluate).
"""

import json
from dataclasses import dataclass

from app.agents.guardrails import sanitize_query
from app.llm.client import LLMClient
from app.rag.tools import EXPAND_GRAPH, KNOWN_TOOLS, RETRIEVE_VEC

DEFAULT_TOOLS = [RETRIEVE_VEC, EXPAND_GRAPH]

REPLAN_SYSTEM = (
    "Ты - планировщик RAG-агента. Пользовательский вопрос не получил достаточного "
    "контекста с первой попытки. Выбери, как добрать информацию: "
    '{"tools": ["retrieve_vec", "expand_graph"], "query": "уточнённый поисковый запрос"}. '
    "retrieve_vec - векторный поиск по чанкам документов; expand_graph - расширение "
    "по графу знаний (связанные акты, понятия). Верни СТРОГО JSON без пояснений."
)


@dataclass(frozen=True)
class Plan:
    tools: list[str]
    query: str


def initial_plan(question: str) -> Plan:
    return Plan(tools=list(DEFAULT_TOOLS), query=question)


def fallback_replan(
    *,
    previous_tools: list[str],
    question: str,
    refined_query: str | None,
) -> Plan:
    """Правила без LLM: сохраняем полный набор инструментов, уточняем запрос."""
    tools = sorted(set(previous_tools) | set(DEFAULT_TOOLS), key=DEFAULT_TOOLS.index)
    query = (refined_query or question).strip() or question
    cleaned, _ = sanitize_query(query)
    return Plan(tools=tools, query=cleaned or question)


async def replan(
    llm: LLMClient,
    *,
    question: str,
    previous_tools: list[str],
    retrieved_summary: str,
    feedback: str,
    refined_query: str | None,
) -> Plan:
    """LLM-планирование второй попытки; любая неудача -> детерминированный фолбэк."""
    user_payload = json.dumps(
        {
            "question": question,
            "previous_tools": previous_tools,
            "retrieved": retrieved_summary,
            "evaluator_feedback": feedback,
        },
        ensure_ascii=False,
    )
    try:
        raw = await llm.complete_json(REPLAN_SYSTEM, user_payload, max_tokens=256)
    except Exception:
        return fallback_replan(
            previous_tools=previous_tools, question=question, refined_query=refined_query
        )

    if not isinstance(raw, dict):
        return fallback_replan(
            previous_tools=previous_tools, question=question, refined_query=refined_query
        )

    requested = raw.get("tools")
    tools = [
        tool
        for tool in (requested if isinstance(requested, list) else [])
        if isinstance(tool, str) and tool in KNOWN_TOOLS
    ]
    if not tools:
        tools = list(DEFAULT_TOOLS)

    query_raw = raw.get("query")
    query = query_raw.strip() if isinstance(query_raw, str) else ""
    if not query:
        query = refined_query or question

    cleaned, _ = sanitize_query(query[:2000])
    return Plan(tools=tools, query=cleaned or question)


__all__ = ["Plan", "fallback_replan", "initial_plan", "replan"]
