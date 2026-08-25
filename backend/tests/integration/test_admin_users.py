"""Интеграция: управление пользователями — POST/GET /admin/users и POST /auth/register.

Проверяется ключевой сценарий задания: два пользователя с разными ролями
имеют разный доступ (clearance viewer=PUBLIC vs analyst=PUBLIC+INTERNAL,
admin-эндпоинты недоступны не-админам).
"""

import uuid

import pytest
from app.config import Settings

from tests.integration.helpers import CREDENTIALS, seed_users

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def seeded_users(base_settings: Settings, pg_dsn: str) -> None:
    await seed_users(base_settings, pg_dsn)


async def _login(client, username: str) -> dict:
    response = await client.post(
        "/auth/login", json={"username": username, "password": CREDENTIALS[username]}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.integration
async def test_admin_creates_two_users_with_different_access(itg_client) -> None:
    """Ядро демонстрации RBAC: viewer и analyst создаются админом и видят разный ACL."""
    client, _app = itg_client
    admin_headers = await _login(client, "admin")

    viewer_name = f"qa_view_{uuid.uuid4().hex[:8]}"
    analyst_name = f"qa_anls_{uuid.uuid4().hex[:8]}"

    created = await client.post(
        "/admin/users",
        headers=admin_headers,
        json={"username": viewer_name, "password": "qaview123", "role": "viewer"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["role"] == "viewer"
    assert body["clearances"] == ["PUBLIC"]
    assert "password_hash" not in body and "password" not in body

    created2 = await client.post(
        "/admin/users",
        headers=admin_headers,
        json={"username": analyst_name, "password": "qaanls123", "role": "analyst"},
    )
    assert created2.status_code == 201, created2.text
    assert created2.json()["clearances"] == ["INTERNAL", "PUBLIC"]

    # дубликат имени → 409
    dup = await client.post(
        "/admin/users",
        headers=admin_headers,
        json={"username": viewer_name, "password": "qaview123", "role": "viewer"},
    )
    assert dup.status_code == 409

    # список содержит обоих; без секретов
    listing = await client.get("/admin/users", headers=admin_headers)
    assert listing.status_code == 200
    usernames = [u["username"] for u in listing.json()["users"]]
    assert viewer_name in usernames and analyst_name in usernames
    assert all("password" not in u for u in listing.json()["users"])

    # оба могут войти и получить свой профиль с корректными метками
    for name, role, clearances, pwd in (
        (viewer_name, "viewer", ["PUBLIC"], "qaview123"),
        (analyst_name, "analyst", ["INTERNAL", "PUBLIC"], "qaanls123"),
    ):
        token = await client.post("/auth/login", json={"username": name, "password": pwd})
        assert token.status_code == 200
        me = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token.json()['access_token']}"},
        )
        assert me.status_code == 200
        profile = me.json()
        assert profile["role"] == role
        assert profile["clearances"] == clearances


@pytest.mark.integration
async def test_create_user_requires_admin(itg_client) -> None:
    """Не-админ не может ни создавать пользователей, ни смотреть список."""
    client, _app = itg_client
    for who in ("viewer", "analyst"):
        headers = await _login(client, who)
        payload = {"username": f"x_{who}", "password": "qwerty123", "role": "viewer"}
        assert (await client.post("/admin/users", headers=headers, json=payload)).status_code == 403
        assert (await client.get("/admin/users", headers=headers)).status_code == 403

    # без токена → 401
    anon = await client.get("/admin/users")
    assert anon.status_code == 401


@pytest.mark.integration
async def test_register_public_creates_viewer(itg_client) -> None:
    """Саморегистрация открыта, но всегда даёт минимальную роль viewer/PUBLIC."""
    client, _app = itg_client
    name = f"self_{uuid.uuid4().hex[:8]}"

    reg = await client.post("/auth/register", json={"username": name, "password": "selfpass123"})
    assert reg.status_code == 201, reg.text
    token_body = reg.json()
    assert token_body["role"] == "viewer"

    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {token_body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["clearances"] == ["PUBLIC"]

    # повторная регистрация того же имени → 409
    again = await client.post("/auth/register", json={"username": name, "password": "selfpass123"})
    assert again.status_code == 409

    # короткий пароль → 422 (валидация контракта)
    weak = await client.post(
        "/auth/register", json={"username": f"w_{uuid.uuid4().hex[:6]}", "password": "short"}
    )
    assert weak.status_code == 422


@pytest.mark.integration
async def test_registered_viewer_cannot_reach_admin(itg_client) -> None:
    client, _app = itg_client
    name = f"self_{uuid.uuid4().hex[:8]}"
    reg = await client.post("/auth/register", json={"username": name, "password": "selfpass123"})
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}

    assert (await client.get("/admin/users", headers=headers)).status_code == 403
    assert (
        await client.post(
            "/admin/users",
            headers=headers,
            json={"username": "escalate", "password": "hack12345", "role": "admin"},
        )
    ).status_code == 403
    assert (await client.get("/admin/stats", headers=headers)).status_code == 403


@pytest.mark.integration
async def test_admin_changes_role_and_access_expands(itg_client) -> None:
    """Смена роли админом: метки доступа пересчитываются, перелогин не нужен."""
    client, _app = itg_client
    admin_headers = await _login(client, "admin")

    name = f"qa_promo_{uuid.uuid4().hex[:8]}"
    created = await client.post(
        "/admin/users",
        headers=admin_headers,
        json={"username": name, "password": "qapromo123", "role": "viewer"},
    )
    user_id = created.json()["user_id"]

    # токен пользователя, выпущенный ДО смены роли
    old_login = await client.post("/auth/login", json={"username": name, "password": "qapromo123"})
    old_headers = {"Authorization": f"Bearer {old_login.json()['access_token']}"}
    assert (await client.get("/auth/me", headers=old_headers)).json()["clearances"] == ["PUBLIC"]

    patched = await client.patch(
        f"/admin/users/{user_id}", headers=admin_headers, json={"role": "analyst"}
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["role"] == "analyst"
    assert body["clearances"] == ["INTERNAL", "PUBLIC"]

    # тот же access-токен: права уже расширены (clearances резолвятся из БД)
    profile = (await client.get("/auth/me", headers=old_headers)).json()
    assert profile["role"] == "analyst"
    assert profile["clearances"] == ["INTERNAL", "PUBLIC"]

    # понижение обратно работает так же
    demoted = await client.patch(
        f"/admin/users/{user_id}", headers=admin_headers, json={"role": "viewer"}
    )
    assert demoted.json()["clearances"] == ["PUBLIC"]

    # несуществующий пользователь → 404
    not_found = await client.patch(
        f"/admin/users/{uuid.UUID(int=0)}", headers=admin_headers, json={"role": "viewer"}
    )
    assert not_found.status_code == 404

    # смена собственной роли запрещена
    admin_me = await client.get("/auth/me", headers=admin_headers)
    self_patch = await client.patch(
        f"/admin/users/{admin_me.json()['user_id']}", headers=admin_headers, json={"role": "viewer"}
    )
    assert self_patch.status_code == 403

    # не-админ не может менять роли
    forbidden = await client.patch(
        f"/admin/users/{user_id}", headers=old_headers, json={"role": "admin"}
    )
    assert forbidden.status_code == 403
