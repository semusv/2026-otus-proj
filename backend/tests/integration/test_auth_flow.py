"""Интеграционные тесты auth-флоу против реальной PG (compose)."""

from collections.abc import Callable

import pytest
from app.config import Settings
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.helpers import CREDENTIALS, seed_users

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def seeded_users(base_settings: Settings, pg_dsn: str) -> None:
    await seed_users(base_settings, pg_dsn)

async def test_login_and_me_roundtrip(itg_client: tuple) -> None:
    client, _ = itg_client
    resp = await client.post(
        "/auth/login", json={"username": "admin", "password": CREDENTIALS["admin"]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "admin"
    assert body["expires_in"] > 0
    assert "X-Trace-Id" in resp.headers

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    profile = me.json()
    assert profile["role"] == "admin"
    assert profile["clearances"] == ["INTERNAL", "PUBLIC", "SECRET"]


async def test_viewer_clearances_public_only(itg_client: tuple) -> None:
    client, _ = itg_client
    token = (
        await client.post(
            "/auth/login", json={"username": "viewer", "password": CREDENTIALS["viewer"]}
        )
    ).json()["access_token"]
    profile = (await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})).json()
    assert profile["role"] == "viewer"
    assert profile["clearances"] == ["PUBLIC"]


async def test_wrong_password_401_structured(itg_client: tuple) -> None:
    client, _ = itg_client
    resp = await client.post("/auth/login", json={"username": "admin", "password": "wrong-pass-1"})
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "invalid_credentials"
    assert "message" in body


async def test_unknown_user_same_error_as_wrong_password(itg_client: tuple) -> None:
    client, _ = itg_client
    resp = await client.post("/auth/login", json={"username": "ghost", "password": "whatever-123"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_credentials"


async def test_me_without_token_401(itg_client: tuple) -> None:
    client, _ = itg_client
    resp = await client.get("/auth/me")
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_token"


async def test_me_with_garbage_token_401(itg_client: tuple) -> None:
    client, _ = itg_client
    resp = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


async def test_login_creates_session_row(
    itg_client: tuple, db_factory: Callable[[], AsyncSession]
) -> None:
    client, app = itg_client
    token = (
        await client.post(
            "/auth/login", json={"username": "analyst", "password": CREDENTIALS["analyst"]}
        )
    ).json()["access_token"]

    from app.core.security import decode_token

    settings = app.state.settings
    claims = decode_token(token, settings.jwt_secret.get_secret_value())

    from app.db.models import AuthSession

    async with db_factory() as session:
        row = await session.get(AuthSession, claims.jti)
        assert row is not None
        assert row.revoked is False


async def test_login_validation_error_shape(itg_client: tuple) -> None:
    client, _ = itg_client
    resp = await client.post("/auth/login", json={"username": "x", "password": ""})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_error"
    assert isinstance(body["details"], list)
