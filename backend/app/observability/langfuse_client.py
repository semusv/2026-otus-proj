"""Интеграция Langfuse (этап 7, ADR-008): промпты/комплиты генераций.

Принципы:
- НИЗКОуровневый API SDK (trace/generation), а не @observe - чтобы не конфликтовать
  с собственным TracerProvider OTel (Jaeger) и не порождать второй глобальный провайдер;
- Единая трасса: id трейса Langfuse = X-Trace-Id запроса (contextvar), session тоже он -
  в UI Langfuse ходы группируются по сессии диалога и склеиваются с Jaeger по значению;
- Graceful: недоступность Langfuse НИКОГДА не ломает чат - все вызовы обёрнуты,
  клиент с таймаутом; при APP_LANGFUSE_ENABLED=false или пустых ключах - no-op;
- Flush на shutdown - best-effort в отдельном потоке.
"""

import asyncio
import logging
from typing import Any

from app.config import Settings

logger = logging.getLogger("app.observability.langfuse")

_CLIENT: Any | None = None


def setup_langfuse(settings: Settings) -> None:
    """Создаёт клиент Langfuse, если включено и заданы креды (идемпотентно)."""
    global _CLIENT
    if _CLIENT is not None or not settings.langfuse_enabled:
        return
    if not (settings.langfuse_url and settings.langfuse_public_key
            and settings.langfuse_secret_key.get_secret_value()):
        logger.warning(
            "APP_LANGFUSE_ENABLED=true, но URL/ключи пусты - интеграция отключена"
        )
        return
    try:
        from langfuse import Langfuse

        _CLIENT = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            host=settings.langfuse_url,
            timeout=10,
        )
        logger.info("Langfuse включён: %s", settings.langfuse_url)
    except Exception:
        _CLIENT = None
        logger.warning("Не удалось инициализировать Langfuse - продолжаем без него",
                       exc_info=True)


def get_langfuse() -> Any | None:
    """Клиент или None (disabled/не инициализирован)."""
    return _CLIENT


def shutdown_langfuse() -> None:
    """Флашит очередь событий на shutdown (best-effort, без блокировки loop'а)."""
    client = _CLIENT
    if client is None:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        client.flush()
        return

    async def _flush() -> None:
        await asyncio.to_thread(client.shutdown)

    try:
        asyncio.get_running_loop().create_task(_flush())
    except Exception:
        logger.debug("Langfuse shutdown-flush пропущен", exc_info=True)


def start_generation(*, name: str, model: str, messages: list[dict[str, str]]) -> Any | None:
    """Открывает generation в трейсе текущего запроса (id=session=X-Trace-Id)."""
    client = _CLIENT
    if client is None:
        return None
    try:
        from app.core.context import get_trace_id, get_user_id

        trace_id = get_trace_id()
        lf_trace = client.trace(
            id=trace_id,
            session_id=trace_id,
            user_id=None if get_user_id() == "-" else get_user_id(),
            name="chat",
        )
        return lf_trace.generation(name=name, model=model, input=messages)
    except Exception:
        logger.debug("start_generation Langfuse пропущен", exc_info=True)
        return None


def end_generation(handle: Any | None, *, output: str, error: str | None = None) -> None:
    """Закрывает generation (best-effort)."""
    if handle is None:
        return
    try:
        if error is not None:
            handle.end(output={"error": error}, level="ERROR")
        else:
            handle.end(output=output)
    except Exception:
        logger.debug("end_generation Langfuse пропущен", exc_info=True)


__all__ = [
    "end_generation",
    "get_langfuse",
    "setup_langfuse",
    "shutdown_langfuse",
    "start_generation",
]
