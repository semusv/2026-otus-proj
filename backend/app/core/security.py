"""Криптоядро аутентификации: bcrypt-хеши, JWT (HS256), резолв роли в ACL-метки.

Интерфейс ``resolve_clearances`` изолирует точку изменения при появлении внешнего IdP
(см. ADR-007).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.errors import TokenExpiredError, TokenInvalidError
from app.db.models import RoleName

JWT_ALGORITHM = "HS256"
TOKEN_TYPE_ACCESS = "access"  # noqa: S105 — тип токена, не пароль

# Роль → допустимые метки доступа (Security-by-Design, pre-fetch ACL; ADR-007)
ROLE_CLEARANCES: dict[RoleName, frozenset[str]] = {
    RoleName.VIEWER: frozenset({"PUBLIC"}),
    RoleName.ANALYST: frozenset({"PUBLIC", "INTERNAL"}),
    RoleName.ADMIN: frozenset({"PUBLIC", "INTERNAL", "SECRET"}),
}


def resolve_clearances(role: RoleName | str) -> list[str]:
    """Возвращает отсортированный список меток доступа для роли."""
    if isinstance(role, str):
        try:
            role = RoleName(role.lower())
        except ValueError:
            raise ValueError(f"Неизвестная роль: {role}") from None
    return sorted(ROLE_CLEARANCES[role])


# --- Пароли (bcrypt) ---


def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


# --- JWT ---


@dataclass(frozen=True)
class TokenClaims:
    sub: uuid.UUID
    role: str
    jti: uuid.UUID
    exp: datetime
    type: str


def create_access_token(
    *,
    user_id: uuid.UUID,
    role: str,
    session_id: uuid.UUID,
    secret: str,
    ttl_minutes: int,
) -> str:
    """Выпускает access-токен; jti = id сессии в PG (возможность отзыва)."""
    now = datetime.now(tz=UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "jti": str(session_id),
        "type": TOKEN_TYPE_ACCESS,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_token(token: str, secret: str) -> TokenClaims:
    """Декодирует и валидирует токен; доменные ошибки вместо библиотечных."""
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub", "jti"]},
        )
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError() from None
    except jwt.InvalidTokenError:
        raise TokenInvalidError() from None

    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise TokenInvalidError()

    try:
        return TokenClaims(
            sub=uuid.UUID(payload["sub"]),
            role=str(payload.get("role", "")),
            jti=uuid.UUID(payload["jti"]),
            exp=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
            type=TOKEN_TYPE_ACCESS,
        )
    except (KeyError, ValueError):
        raise TokenInvalidError() from None
