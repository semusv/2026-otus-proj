"""Тесты ядра безопасности: bcrypt, JWT roundtrip/ошибки, resolve_clearances."""

import uuid
from datetime import UTC, datetime

import pytest
from app.core.errors import TokenExpiredError, TokenInvalidError
from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
    resolve_clearances,
    verify_password,
)
from app.db.models import RoleName

pytestmark = pytest.mark.unit

SECRET = "unit-secret-0123456789abcdef"


def test_password_hash_roundtrip() -> None:
    h = hash_password("S3cret-Pass!")
    assert h != "S3cret-Pass!"
    assert verify_password("S3cret-Pass!", h)
    assert not verify_password("wrong-password", h)


def test_password_hash_unique_salts() -> None:
    assert hash_password("same") != hash_password("same")


def test_jwt_roundtrip() -> None:
    user_id, session_id = uuid.uuid4(), uuid.uuid4()
    token = create_access_token(
        user_id=user_id, role="analyst", session_id=session_id, secret=SECRET, ttl_minutes=5
    )
    claims = decode_token(token, SECRET)
    assert claims.sub == user_id
    assert claims.jti == session_id
    assert claims.role == "analyst"
    assert claims.exp > datetime.now(UTC)


def test_expired_token_rejected() -> None:
    token = create_access_token(
        user_id=uuid.uuid4(),
        role="viewer",
        session_id=uuid.uuid4(),
        secret=SECRET,
        ttl_minutes=-1,
    )
    with pytest.raises(TokenExpiredError):
        decode_token(token, SECRET)


def test_tampered_signature_rejected() -> None:
    token = create_access_token(
        user_id=uuid.uuid4(),
        role="admin",
        session_id=uuid.uuid4(),
        secret=SECRET,
        ttl_minutes=5,
    )
    with pytest.raises(TokenInvalidError):
        decode_token(token + "x", SECRET)


def test_wrong_secret_rejected() -> None:
    token = create_access_token(
        user_id=uuid.uuid4(),
        role="admin",
        session_id=uuid.uuid4(),
        secret=SECRET,
        ttl_minutes=5,
    )
    with pytest.raises(TokenInvalidError):
        decode_token(token, "another-secret-0123456789")


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (RoleName.VIEWER, ["PUBLIC"]),
        (RoleName.ANALYST, ["INTERNAL", "PUBLIC"]),
        (RoleName.ADMIN, ["INTERNAL", "PUBLIC", "SECRET"]),
        ("admin", ["INTERNAL", "PUBLIC", "SECRET"]),
    ],
)
def test_resolve_clearances(role: object, expected: list[str]) -> None:
    assert sorted(resolve_clearances(role)) == expected  # type: ignore[arg-type]


def test_resolve_clearances_unknown_role() -> None:
    with pytest.raises(ValueError):
        resolve_clearances("superhacker")
