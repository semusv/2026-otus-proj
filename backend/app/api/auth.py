"""Auth-эндпоинты: POST /auth/login, GET /auth/me (ADR-007)."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.api.deps import audit_context, get_current_user
from app.config import Settings
from app.core.errors import InvalidCredentialsError
from app.core.security import create_access_token, resolve_clearances, verify_password
from app.db.base import get_session, new_pk
from app.db.models import AuditLog, AuthSession, User
from app.schemas.auth import LoginRequest, MeResponse, TokenResponse
from app.schemas.errors import ErrorResponse

router = APIRouter(prefix="/auth", tags=["auth"])

_401_LOGIN = {
    "model": ErrorResponse,
    "description": "Неверные учётные данные или пользователь деактивирован",
}
_401_TOKEN = {
    "model": ErrorResponse,
    "description": "Токен отсутствует, недействителен или истёк",
}


async def _audit(
    session: AsyncSession,
    action: str,
    user_id: object | None = None,
    detail: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditLog(
            action=action,
            user_id=user_id,
            detail=detail or {},
            **audit_context(),
        )
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={401: _401_LOGIN},
    summary="Вход: выдаёт JWT (HS256) и создаёт сессию в PG",
)
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    settings: Settings = request.app.state.settings

    user = (
        await session.execute(
            select(User).options(joinedload(User.role)).where(User.username == payload.username)
        )
    ).scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        await _audit(session, "auth.login_failed", None, {"username": payload.username})
        await session.commit()
        raise InvalidCredentialsError()
    if not user.is_active:
        await _audit(session, "auth.login_failed", user.id, {"reason": "inactive"})
        await session.commit()
        raise InvalidCredentialsError("Пользователь деактивирован")

    ttl_minutes = settings.jwt_ttl_minutes
    auth_session = AuthSession(
        id=new_pk(),
        user_id=user.id,
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
    )
    token = create_access_token(
        user_id=user.id,
        role=user.role.name.value,
        session_id=auth_session.id,
        secret=settings.jwt_secret.get_secret_value(),
        ttl_minutes=ttl_minutes,
    )
    session.add(auth_session)
    await _audit(session, "auth.login", user.id, {"session_id": str(auth_session.id)})
    await session.commit()

    return TokenResponse(
        access_token=token,
        expires_in=ttl_minutes * 60,
        username=user.username,
        role=user.role.name.value,
    )


@router.get(
    "/me",
    response_model=MeResponse,
    responses={401: _401_TOKEN},
    summary="Профиль текущего пользователя по Bearer-токену",
)
async def me(user: User = Depends(get_current_user)) -> MeResponse:
    return MeResponse(
        user_id=str(user.id),
        username=user.username,
        role=user.role.name.value,
        clearances=resolve_clearances(user.role.name),
    )
