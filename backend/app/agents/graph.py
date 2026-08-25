"""Топология агента: state machine на LangGraph (НЕ линейная цепочка; ADR-005).

Узлы: guardrail_in -> planner -> tools[retrieve_vec|expand_graph] -> fusion_rerank
-> generate (vLLM stream) -> evaluate -> (re-plan | guardrail_out) -> END.

Agent Loop: после generate узел ``evaluate`` проверяет достаточность контекста;
при нехватке и неисчерпанном лимите - возврат к planner с уточнённым запросом
(максимум APP_AGENT_MAX_ITERATIONS re-plan'ов).

Компоненты по заданию: Memory (agents/memory.py), Planner (agents/planner.py),
Tools Interface (rag/tools.py).
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.guardrails import (
    ContextSource,
    build_context_block,
    check_injection_heuristics,
    classify_injection_llm,
    classify_output_llm,
    enforce_acl_boundary,
    extract_citations,
    refusal_answer,
    sanitize_query,
)
from app.agents.memory import ChatMemory
from app.agents.planner import Plan, initial_plan, replan
from app.config import Settings
from app.llm.client import LLMClient
from app.observability.tracing import traced_node
from app.rag.fusion import fuse_rankings
from app.rag.reranker import Reranker
from app.rag.retrievers import RetrievedChunk
from app.rag.tools import EXPAND_GRAPH, RETRIEVE_VEC, AgentTools, ToolOutput

GENERATION_SYSTEM = (
    "Ты - ассистент по нормативным документам РФ. Отвечай ТОЛЬКО на основе "
    "предоставленных источников. Каждый фактический тезис сопровождай ссылкой "
    "на источник в формате [S1], [S2]. Если информации в источниках недостаточно - "
    "прямо скажи об этом. Отвечай по-русски, кратко и по делу."
)

logger = logging.getLogger("app.agents.graph")

EVALUATE_SYSTEM = (
    "Ты - контролёр качества RAG-ответа. Оцени, достаточно ли найденного контекста "
    "для ответа на вопрос пользователя. Ответь СТРОГО JSON без пояснений: "
    '{"sufficient": true|false, "feedback": "что не хватает, кратко", '
    '"refined_query": "уточнённый поисковый запрос или пустая строка"}'
)

EMPTY_ANSWER = (
    "По вашему запросу в доступных вам источниках ничего не найдено. "
    "Попробуйте переформулировать вопрос."
)


class ChatEventSink(Protocol):
    """Приёмник событий узлов графа (SSE-стриминг); no-op в тестах."""

    async def emit(self, event: str, data: dict[str, object]) -> None: ...


class NullSink:
    """Заглушка для non-stream режима и unit-тестов."""

    async def emit(self, event: str, data: dict[str, object]) -> None:
        _ = event, data


class QueueSink:
    """Перекладывает события графа в очередь для SSE-генератора эндпоинта."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[str, dict[str, object]]] = asyncio.Queue()

    async def emit(self, event: str, data: dict[str, object]) -> None:
        await self.queue.put((event, data))


class AgentState(TypedDict, total=False):
    """Состояние графа; ключи опциональны - узлы дописывают по мере прохода."""

    original_question: str
    question: str
    clearances: list[str]
    session_id: str
    history: list[dict[str, str]]
    tools: list[str]
    plan_query: str
    vector_hits: list[RetrievedChunk]
    graph_chunks: list[RetrievedChunk]
    expansion_related_acts: list[dict[str, str]]
    expansion_context_text: str
    sources: list[ContextSource]
    context_block: str
    answer: str
    citations: list[dict[str, object]]
    replans: int
    evaluate_sufficient: bool
    evaluate_feedback: str
    evaluate_refined_query: str
    status: str
    notes: list[str]
    injection_issues: list[str]


@dataclass
class AgentRuntime:
    """Сервисы агента; атрибуты читаются узлами лениво (подмена в тестах)."""

    settings: Settings
    llm: LLMClient
    tools: AgentTools
    reranker: Reranker
    memory: ChatMemory
    extra: dict[str, Any] = field(default_factory=dict)


def _dedup_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    seen: dict[tuple[str, int], RetrievedChunk] = {}
    for chunk in chunks:
        key = (chunk.act_id, chunk.chunk_no)
        current = seen.get(key)
        if current is None or chunk.score > current.score:
            seen[key] = chunk
    return list(seen.values())


def _merge_expansion(
    current: list[dict[str, str]], incoming: list[dict[str, str]]
) -> list[dict[str, str]]:
    merged = {item["id"]: item for item in current}
    for item in incoming:
        merged.setdefault(item["id"], item)
    return list(merged.values())


def _retrieved_summary(state: AgentState) -> str:
    hits = state.get("vector_hits", [])[:5]
    listed = "; ".join(f"{chunk.title} ({chunk.act_id})" for chunk in hits) or "ничего"
    acts = state.get("expansion_related_acts", [])
    extra = f"; соседи по графу: {len(acts)}" if acts else ""
    return f"векторные хиты: {listed}{extra}"


def build_agent_graph(runtime: AgentRuntime, *, sink: ChatEventSink | None = None) -> Any:
    """Компилирует граф агента. Sink подаётся per-request (SSE/no-op)."""
    events = sink or NullSink()
    cfg = runtime.settings

    async def _emit(stage: str, **data: object) -> None:
        await events.emit("status", {"stage": stage, **data})

    @traced_node("guardrail_in")
    async def guardrail_in(state: AgentState) -> dict[str, Any]:
        await _emit("guardrails")
        notes = list(state.get("notes", []))
        cleaned, modified = sanitize_query(state["original_question"])
        if modified:
            notes.append("query_sanitized")
        if len(cleaned) > cfg.guardrail_max_query_chars:
            cleaned = cleaned[: cfg.guardrail_max_query_chars]
            notes.append("query_truncated")

        issues = check_injection_heuristics(cleaned)
        if not issues and cfg.guardrail_use_llm and cleaned:
            blocked, reason = await classify_injection_llm(runtime.llm, cleaned)
            if reason == "classifier_unavailable":
                notes.append("guardrail_classifier_unavailable")
            elif blocked:
                issues = ["llm_injection_classifier"]
                notes.append(f"injection_reason:{reason}"[:120])

        return {
            "question": cleaned,
            "notes": notes,
            "injection_issues": issues,
        }

    def route_after_guardrail(state: AgentState) -> str:
        return "refuse" if state.get("injection_issues") else "continue"

    @traced_node("refuse")
    async def refuse(state: AgentState) -> dict[str, Any]:
        return {
            "answer": refusal_answer(),
            "citations": [],
            "status": "degraded",
            "notes": [f"injection_blocked:{','.join(state.get('injection_issues', []))}"],
        }

    @traced_node("planner")
    async def planner_node(state: AgentState) -> dict[str, Any]:
        replanned_before = bool(state.get("plan_query"))
        replans = state.get("replans", 0)
        if not replanned_before:
            plan: Plan = initial_plan(state["question"])
            await _emit("planner", iteration=0, tools=plan.tools)
            return {"tools": plan.tools, "plan_query": plan.query, "replans": 0}

        plan = await replan(
            runtime.llm,
            question=state["question"],
            previous_tools=state.get("tools", []),
            retrieved_summary=_retrieved_summary(state),
            feedback=state.get("evaluate_feedback", ""),
            refined_query=state.get("evaluate_refined_query") or None,
        )
        done_replans = replans + 1
        await _emit("planner", iteration=done_replans, tools=plan.tools)
        return {
            "tools": plan.tools,
            "plan_query": plan.query,
            "replans": done_replans,
            "notes": [*[f"replan_{done_replans}:{plan.query}"[:160]], *state.get("notes", [])],
        }

    @traced_node("tools")
    async def tools_node(state: AgentState) -> dict[str, Any]:
        await _emit("retrieve", tools=state["tools"])
        outputs: list[ToolOutput] = await runtime.tools.run(
            state["tools"],
            state["plan_query"],
            clearances=state["clearances"],
        )
        vector_hits = list(state.get("vector_hits", []))
        graph_chunks = list(state.get("graph_chunks", []))
        related_acts = list(state.get("expansion_related_acts", []))
        expansion_texts = (
            [state["expansion_context_text"]] if state.get("expansion_context_text") else []
        )
        for output in outputs:
            if output.name == RETRIEVE_VEC:
                vector_hits.extend(output.chunks)
            elif output.name == EXPAND_GRAPH:
                graph_chunks.extend(output.chunks)
                if output.expansion is not None:
                    related_acts = _merge_expansion(
                        related_acts,
                        [
                            {"id": act.id, "title": act.title, "clearance": act.clearance}
                            for act in output.expansion.related_acts
                        ],
                    )
                    text = output.expansion.as_context_text()
                    if text:
                        expansion_texts.append(text)
        return {
            "vector_hits": _dedup_chunks(vector_hits),
            "graph_chunks": _dedup_chunks(graph_chunks),
            "expansion_related_acts": related_acts,
            "expansion_context_text": "\n".join(expansion_texts),
        }

    @traced_node("fusion_rerank")
    async def fusion_rerank_node(state: AgentState) -> dict[str, Any]:
        await _emit("fusion_rerank")
        fused = _dedup_chunks(
            fuse_rankings(state.get("vector_hits", []), state.get("graph_chunks", []))
        )
        rerank_input = fused[: cfg.rag_vector_top_k * 2]
        top = await runtime.reranker.rerank(
            state["plan_query"], rerank_input, top_n=cfg.rag_final_top_n
        )
        sources = [
            ContextSource(
                source_id=f"S{index}",
                act_id=chunk.act_id,
                title=chunk.title,
                chunk_no=chunk.chunk_no,
                clearance=chunk.clearance,
                text=chunk.text,
            )
            for index, chunk in enumerate(top, start=1)
        ]
        context_block = build_context_block(
            sources, state.get("expansion_context_text", "")
        )
        return {"sources": sources, "context_block": context_block}

    @traced_node("generate")
    async def generate_node(state: AgentState) -> dict[str, Any]:
        if not state.get("sources"):
            await _emit("generate", mode="empty")
            return {"answer": EMPTY_ANSWER, "status": "empty"}

        await _emit("generate")
        context_block = state.get("context_block", "")
        user_content = (
            f"Источники:\n{context_block}\n\nВопрос пользователя: {state['question']}"
        )
        messages: list[dict[str, str]] = [{"role": "system", "content": GENERATION_SYSTEM}]
        messages.extend(state.get("history", []))
        messages.append({"role": "user", "content": user_content})

        parts: list[str] = []
        async for delta in runtime.llm.stream(
            messages,
            temperature=cfg.generate_temperature,
            max_tokens=cfg.generate_max_tokens,
        ):
            parts.append(delta)
            await events.emit("token", {"delta": delta})
        return {"answer": "".join(parts)}

    @traced_node("evaluate")
    async def evaluate_node(state: AgentState) -> dict[str, Any]:
        await _emit("evaluate")
        notes = list(state.get("notes", []))
        payload = json.dumps(
            {
                "question": state["question"],
                "found_sources": [
                    f"[{source.source_id}] {source.title}" for source in state.get("sources", [])
                ],
                "answer_preview": state.get("answer", "")[:600],
            },
            ensure_ascii=False,
        )
        try:
            verdict = await runtime.llm.complete_json(EVALUATE_SYSTEM, payload, max_tokens=128)
        except Exception:
            verdict = None
            notes.append("evaluate_unavailable")

        sufficient = bool(isinstance(verdict, dict) and verdict.get("sufficient"))
        if sufficient:
            return {"evaluate_sufficient": True, "notes": notes}

        if state.get("replans", 0) >= cfg.agent_max_iterations:
            notes.append("max_replans_reached")
            return {"evaluate_sufficient": False, "notes": notes}

        feedback = ""
        refined = ""
        if isinstance(verdict, dict):
            feedback = str(verdict.get("feedback", ""))[:300]
            refined_raw = verdict.get("refined_query")
            refined = refined_raw.strip()[:300] if isinstance(refined_raw, str) else ""
        return {
            "evaluate_sufficient": False,
            "evaluate_feedback": feedback,
            "evaluate_refined_query": refined,
            "notes": notes,
        }

    def route_after_evaluate(state: AgentState) -> str:
        exhausted = state.get("replans", 0) >= cfg.agent_max_iterations
        return "finish" if state.get("evaluate_sufficient") or exhausted else "replan"

    @traced_node("guardrail_out")
    async def guardrail_out_node(state: AgentState) -> dict[str, Any]:
        await _emit("guardrails_out")
        notes = list(state.get("notes", []))
        answer = state.get("answer", "")

        # финальный ACL-инвариант (этап 6): ни один источник/сосед вне допустимых
        # меток не покидает границу API - независимо от поведения retrieval
        acl = enforce_acl_boundary(
            state.get("sources", []),
            state.get("expansion_related_acts", []),
            state.get("clearances", []),
        )
        status = state.get("status", "ok")
        if acl.violated:
            notes.append(f"acl_violation_dropped:{','.join(acl.dropped_ids)}"[:160])
            status = "degraded"
            audit_acl = runtime.extra.get("audit_acl_violation")
            if audit_acl is not None:
                try:
                    await audit_acl(
                        {"session_id": state.get("session_id"), "dropped_ids": acl.dropped_ids}
                    )
                except Exception:  # аудит best-effort: сбой записи не влияет на ответ
                    logger.warning("Не удалось записать acl_violation в аудит", exc_info=True)

        checked = extract_citations(answer, acl.sources)

        status = state.get("status", "ok")
        if checked.dropped_citations:
            notes.append("dropped_citations:" + ",".join(checked.dropped_citations))
            status = "degraded"

        if (
            cfg.guardrail_use_llm
            and status != "empty"
            and checked.clean_answer
            and not state.get("injection_issues")
        ):
            violated, reason = await classify_output_llm(runtime.llm, checked.clean_answer)
            if reason == "classifier_unavailable":
                notes.append("output_guard_classifier_unavailable")
            elif violated:
                notes.append(f"output_violation:{reason}"[:120])
                status = "degraded"

        if status != "empty" and any(
            note in {"max_replans_reached", "evaluate_unavailable"} for note in notes
        ):
            status = "degraded"

        return {
            "answer": checked.clean_answer,
            "citations": checked.citations,
            "expansion_related_acts": acl.related_acts,
            "status": status,
            "notes": notes,
        }

    builder = StateGraph(AgentState)
    builder.add_node("guardrail_in", guardrail_in)
    builder.add_node("refuse", refuse)
    builder.add_node("planner", planner_node)
    builder.add_node("tools", tools_node)
    builder.add_node("fusion_rerank", fusion_rerank_node)
    builder.add_node("generate", generate_node)
    builder.add_node("evaluate", evaluate_node)
    builder.add_node("guardrail_out", guardrail_out_node)

    builder.add_edge(START, "guardrail_in")
    builder.add_conditional_edges(
        "guardrail_in", route_after_guardrail, {"refuse": "refuse", "continue": "planner"}
    )
    builder.add_edge("refuse", END)
    builder.add_edge("planner", "tools")
    builder.add_edge("tools", "fusion_rerank")
    builder.add_edge("fusion_rerank", "generate")
    builder.add_edge("generate", "evaluate")
    builder.add_conditional_edges(
        "evaluate", route_after_evaluate, {"replan": "planner", "finish": "guardrail_out"}
    )
    builder.add_edge("guardrail_out", END)
    return builder.compile()


__all__ = [
    "EMPTY_ANSWER",
    "AgentRuntime",
    "AgentState",
    "ChatEventSink",
    "NullSink",
    "QueueSink",
    "build_agent_graph",
]
