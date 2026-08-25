"""Интеграция Langfuse (этап 7, ADR-008): промпты/комплиты генераций.

Принципы:
- v4 SDK: generation через ``start_as_current_observation(as_type="generation")``,
  сессия/пользователь - через ``propagate_attributes`` (см. docs/observability/features/sessions);
  НЕ используем собственный TracerProvider SDK - его процессор добавляется к нашему,
  Jaeger остаётся единственным владельцем глобального провайдера;
- Единая трасса: OTel trace_id == X-Trace-Id (middleware этапа 7), поэтому трейс в
  Langfuse склеивается с Jaeger по значению; session_id = X-Trace-Id запроса;
- Graceful: недоступность Langfuse НИКОГДА не ломает чат - все вызовы обёрнуты,
  клиент с таймаутом; при APP_LANGFUSE_ENABLED=false или пустых ключах - no-op;
- Flush/shutdown - best-effort в отдельном потоке.
"""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
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
        loop = asyncio.get_running_loop()
    except RuntimeError:
        client.flush()
        return
    task = loop.create_task(asyncio.to_thread(client.shutdown))
    _ = task  # fire-and-forget: приложение завершается после yield


@contextmanager
def llm_generation(
    *, name: str, model: str, messages: list[dict[str, str]]
) -> Iterator[Any | None]:
    """Generation-контекст текущего LLM-вызова; yields handle или None (disabled).

    session_id/user_id берутся из contextvar'ов запроса; любые ошибки Langfuse
    глушатся с debug-логом.
    """
    handle: Any | None = None
    stack: ExitStack | None = None
    client = get_langfuse()
    if client is not None:
        try:
            from langfuse import propagate_attributes

            from app.core.context import get_trace_id, get_user_id

            trace_id = get_trace_id()
            user_id = get_user_id()
            stack = ExitStack()
            stack.enter_context(
                propagate_attributes(
                    session_id=trace_id if trace_id != "-" else None,
                    user_id=user_id if user_id != "-" else None,
                )
            )
            handle = stack.enter_context(
                client.start_as_current_observation(
                    as_type="generation",
                    name=name,
                    model=model,
                    input=messages,
                )
            )
        except Exception:
            logger.debug("llm_generation: контекст Langfuse не создан", exc_info=True)
            if stack is not None:
                stack.close()
            handle = None
    try:
        yield handle
    finally:
        if stack is not None:
            try:
                stack.close()
            except Exception:
                logger.debug("llm_generation: закрытие контекста пропущено", exc_info=True)


def finish_generation(handle: Any | None, *, output: str, error: str | None = None) -> None:
    """Записывает результат generation (best-effort)."""
    if handle is None:
        return
    try:
        if error is not None:
            handle.update(output={"error": error}, level="ERROR",
                          status_message=error[:200])
        else:
            handle.update(output=output)
    except Exception:
        logger.debug("finish_generation Langfuse пропущен", exc_info=True)


__all__ = [
    "finish_generation",
    "get_langfuse",
    "llm_generation",
    "setup_langfuse",
    "shutdown_langfuse",
]
