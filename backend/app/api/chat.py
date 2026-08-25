"""Чат-эндпоинт агента: POST /api/chat (SSE-стриминг + non-stream fallback).

Память диалога - PostgreSQL (agents/memory.py); граф компилируется на запрос
с per-request sink'ом событий. JWT обязателен; clearances берутся из роли
(pre-fetch ACL внутри ретриверов, ADR-007).
"""

import asyncio
import logging
import uuid as uuidlib
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.agents.graph import (
    AgentRuntime,
    QueueSink,
    build_agent_graph,
)
from app.api.deps import audit_context, get_current_user
from app.core.context import get_trace_id
from app.db.models import User
from app.observability.tracing import get_tracer
from app.schemas.chat import ChatRequest, ChatResponse, CitationOut, sse_format

logger = logging.getLogger("app.api.chat")

router = APIRouter(prefix="/api", tags=["chat"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _runtime(request: Request) -> AgentRuntime:
    runtime: AgentRuntime | None = getattr(request.app.state, "chat_runtime", None)
    if runtime is None:
        raise RuntimeError("Chat runtime не инициализирован (create_app)")
    return runtime


def _initial_state(runtime: AgentRuntime, user: User, body: ChatRequest,
                   history: list[dict[str, str]], session_id: uuidlib.UUID) -> dict[str, object]:
    from app.core.security import resolve_clearances

    return {
        "original_question": body.message,
        "clearances": resolve_clearances(user.role.name),
        "session_id": str(session_id),
        "history": history,
    }


async def _stream_generator(
    runtime: AgentRuntime,
    task: asyncio.Task[dict[str, Any]],
    sink: QueueSink,
    *,
    session_id: uuidlib.UUID,
    question: str,
) -> AsyncIterator[str]:
    """Дренаж очереди событий графа -> SSE; финализирует ход в памяти диалога."""
    while True:
        if not sink.queue.empty():
            event, data = sink.queue.get_nowait()
            yield sse_format(event, data)
            continue
        if task.done():
            break
        event, data = await sink.queue.get()
        yield sse_format(event, data)

    while not sink.queue.empty():
        event, data = sink.queue.get_nowait()
        yield sse_format(event, data)

    exc = task.exception()
    if exc is not None:
        logger.exception("Ошибка графа чата", exc_info=exc)
        yield sse_format("error", {"code": "chat_failed", "message": "Ошибка обработки запроса"})
        return

    final = task.result()
    answer = str(final.get("answer", ""))
    citations: list[dict[str, object]] = list(final.get("citations", []))
    trace_id = get_trace_id()

    try:
        await runtime.memory.save_turn(
            uuidlib.UUID(str(final.get("session_id", session_id))),
            question=question,
            answer=answer,
            citations=citations,
            trace_id=trace_id,
        )
    except Exception:
        logger.exception("Не удалось сохранить ход диалога")

    payload: dict[str, object] = {
        "session_id": final.get("session_id", str(session_id)),
        "answer": answer,
        "citations": citations,
        "status": final.get("status", "degraded"),
        "replans": final.get("replans", 0),
        "related_acts": final.get("expansion_related_acts", []),
        "notes": final.get("notes", []),
        "trace_id": trace_id,
    }
    yield sse_format("done", payload)


@router.post(
    "/chat",
    summary="Вопрос агенту GraphRAG (SSE-стриминг или JSON при stream=false)",
    description=(
        "Конвейер: guardrails -> planner -> retrieval (вектор+граф, ACL pre-fetch) "
        "-> fusion -> rerank -> generate -> evaluate (re-plan <= APP_AGENT_MAX_ITERATIONS) "
        "-> guardrail_out (проверка цитат).\n\n"
        "При stream=true возвращает text/event-stream: события `status`, `token`, `done`, "
        "`error` (формат - event:\\ndata:\\n\\n). При stream=false - обычный JSON."
    ),
    responses={401: {"description": "Нет/невалидный JWT"}},
)
async def chat(
    request: Request,
    body: ChatRequest,
    user: User = Depends(get_current_user),
) -> object:
    runtime = _runtime(request)
    _ = audit_context()  # материализуем correlation-контекст для логов узлов

    chat_session = await runtime.memory.ensure_session(
        user, body.session_id, title=body.message[:200]
    )
    history = await runtime.memory.load_history(
        chat_session.id, runtime.settings.chat_history_limit
    )

    with get_tracer().start_as_current_span("chat.request") as span:
        span.set_attribute("chat.session_id", str(chat_session.id))
        span.set_attribute("chat.role", str(user.role.name))
        span.set_attribute("chat.stream", body.stream)

        graph = build_agent_graph(runtime)
        initial_state = _initial_state(runtime, user, body, history, chat_session.id)

        if not body.stream:
            final = await graph.ainvoke(initial_state)
            trace_id = get_trace_id()
            await runtime.memory.save_turn(
                chat_session.id,
                question=body.message,
                answer=str(final.get("answer", "")),
                citations=list(final.get("citations", [])),
                trace_id=trace_id,
            )
            return ChatResponse(
                session_id=chat_session.id,
                answer=str(final.get("answer", "")),
                citations=[CitationOut.model_validate(item) for item in final.get("citations", [])],
                status=str(final.get("status", "degraded")),
                replans=int(final.get("replans", 0)),
                related_acts=list(final.get("expansion_related_acts", [])),
                notes=list(final.get("notes", [])),
                trace_id=trace_id,
            )

        sink = QueueSink()
        task = asyncio.create_task(graph.ainvoke(initial_state))
        return StreamingResponse(
            _stream_generator(runtime, task, sink, session_id=chat_session.id,
                              question=body.message),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )


__all__ = ["router"]
