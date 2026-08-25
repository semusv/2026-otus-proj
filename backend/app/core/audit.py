"""Аудит отказов доступа в audit_log (этап 6, Security-by-Design).

Best-effort по контракту: сбой записи аудита НИКОГДА не ломает основной ответ -
ошибка логируется, функция возвращает False. Вызывается из центрального
error-обработчика (401/403) и из guardrail_out графа (acl_violation).
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.context import get_request_id, get_trace_id
from app.db.models import AuditLog

logger = logging.getLogger("app.audit")

ACTION_ACCESS_DENIED = "access_denied"
ACTION_ACL_VIOLATION = "acl_violation"
ACTION_GUARDRAIL_EVENT = "guardrail_event"

AuditCallback = Callable[[dict[str, Any]], Awaitable[bool]]


async def record_denial(
    session_factory: Any,
    *,
    action: str,
    user_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> bool:
    """Пишет запись об отказе доступа; любые сбои глушатся с warning'ом."""
    try:
        async with session_factory() as session:
            session.add(
                AuditLog(
                    user_id=user_id,
                    action=action,
                    detail=detail,
                    request_id=get_request_id(),
                    trace_id=get_trace_id(),
                )
            )
            await session.commit()
        return True
    except Exception:
        logger.warning("Не удалось записать аудит отказа (action=%s)", action, exc_info=True)
        return False


__all__ = [
    "ACTION_ACCESS_DENIED",
    "ACTION_ACL_VIOLATION",
    "ACTION_GUARDRAIL_EVENT",
    "AuditCallback",
    "record_denial",
]
