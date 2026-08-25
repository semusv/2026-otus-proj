"""Общие зависимости API: текущий пользователь по Bearer-токену."""

from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.config import Settings
from app.core.context import get_request_id, get_trace_id, set_user_id
from app.core.errors import TokenInvalidError
from app.core.security import decode_token
from app.db.models import AuthSession, User


def extract_bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise TokenInvalidError("Отсутствует Bearer-токен")
    return header[7:].strip()


async def get_current_user(request: Request) -> User:
    """Валидирует JWT и сессию в PG, возвращает активного пользователя."""
    settings: Settings = request.app.state.settings
    claims = decode_token(extract_bearer_token(request), settings.jwt_secret.get_secret_value())

    db = request.app.state.db
    async with db.session_factory() as session:
        auth_session = await session.get(AuthSession, claims.jti)
        expired = auth_session.expires_at <= datetime.now(UTC)
        if auth_session is None or auth_session.revoked or expired:
            raise TokenInvalidError("Сессия недействительна или отозвана")

        user = (
            await session.execute(
                select(User).options(joinedload(User.role)).where(User.id == claims.sub)
            )
        ).scalar_one_or_none()
        if user is None or not user.is_active:
            raise TokenInvalidError()
        _ = user.role.name  # материализуем связь до закрытия сессии
        set_user_id(str(user.id))  # этап 7: user_id в каждой лог-строке запроса
        return user


def audit_context() -> dict[str, str]:
    """Корреляционные идентификаторы для записей аудита."""
    return {"request_id": get_request_id(), "trace_id": get_trace_id()}


__all__ = ["audit_context", "extract_bearer_token", "get_current_user"]
