"""Интеграция: жизненный цикл учётной записи — деактивация, реактивация, удаление.

Бэклог п.6: soft delete по умолчанию (вход заблокирован, история/аудит целы),
hard=true — физическое удаление с чисткой FK (chat_messages → chat_sessions →
sessions) и обезличиванием audit_log; защиты self-delete и последнего админа.
"""

import uuid

import pytest
from app.config import Settings
from app.db.models import AuditLog, AuthSession, ChatMessage, ChatSession, RoleName, User
from app.db.users import other_active_admin_exists
from sqlalchemy import func, select

from tests.integration.helpers import CREDENTIALS, seed_users

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def seeded_users(base_settings: Settings, pg_dsn: str) -> None:
    await seed_users(base_settings, pg_dsn)


async def _login(client, username: str, password: str | None = None) -> dict:
    response = await client.post(
        "/auth/login",
        json={"username": username, "password": password or CREDENTIALS[username]},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _create_user(client, admin_headers: dict, prefix: str, role: str = "viewer") -> dict:
    name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    created = await client.post(
        "/admin/users",
        headers=admin_headers,
        json={"username": name, "password": "qalife1234", "role": role},
    )
    assert created.status_code == 201, created.text
    return created.json()


@pytest.mark.integration
async def test_deactivation_blocks_login_and_existing_token(itg_client) -> None:
    """Деактивация: login → 401, существующий JWT → 401; реактивация восстанавливает."""
    client, _app = itg_client
    admin_headers = await _login(client, "admin")
    user = await _create_user(client, admin_headers, "qa_life")
    user_id = user["user_id"]
    user_headers = await _login(client, user["username"], "qalife1234")
    assert (await client.get("/auth/me", headers=user_headers)).status_code == 200

    deactivated = await client.patch(
        f"/admin/users/{user_id}/status", headers=admin_headers, json={"is_active": False}
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["is_active"] is False

    # вход заблокирован (401 invalid_credentials), старый токен отклонён (401)
    blocked = await client.post(
        "/auth/login", json={"username": user["username"], "password": "qalife1234"}
    )
    assert blocked.status_code == 401
    assert (await client.get("/auth/me", headers=user_headers)).status_code == 401

    # история и имя целы: список содержит запись, реактивация работает
    listing = await client.get("/admin/users", headers=admin_headers)
    target = next(u for u in listing.json()["users"] if u["user_id"] == user_id)
    assert target["is_active"] is False and target["role"] == "viewer"

    reactivated = await client.patch(
        f"/admin/users/{user_id}/status", headers=admin_headers, json={"is_active": True}
    )
    assert reactivated.json()["is_active"] is True
    restored = await client.post(
        "/auth/login", json={"username": user["username"], "password": "qalife1234"}
    )
    assert restored.status_code == 200


@pytest.mark.integration
async def test_soft_delete_deactivates_and_keeps_history(db_factory, itg_client) -> None:
    """DELETE без hard: деактивация; диалоги/сессии/аудит не тронуты."""
    client, _app = itg_client
    admin_headers = await _login(client, "admin")
    user = await _create_user(client, admin_headers, "qa_soft")
    user_id = uuid.UUID(user["user_id"])

    user_headers = await _login(client, user["username"], "qalife1234")

    async with db_factory() as session:
        session.add(ChatSession(id=uuid.uuid4(), user_id=user_id, title="life"))
        await session.commit()

    deleted = await client.delete(f"/admin/users/{user_id}", headers=admin_headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["mode"] == "deactivated"

    async with db_factory() as session:
        uid = user_id
        users_left = await session.scalar(
            select(func.count()).select_from(User).where(User.id == uid)
        )
        assert users_left == 1
        chats_left = await session.scalar(
            select(func.count()).select_from(ChatSession).where(ChatSession.user_id == uid)
        )
        assert chats_left == 1
        # auth-сессия НЕ отозвана физически, но токен отклоняется через is_active
        auths_left = await session.scalar(
            select(func.count()).select_from(AuthSession).where(AuthSession.user_id == uid)
        )
        assert auths_left >= 1
    assert (await client.get("/auth/me", headers=user_headers)).status_code == 401


@pytest.mark.integration
async def test_hard_delete_cascades_and_frees_username(db_factory, itg_client) -> None:
    """DELETE ?hard=true: строки удалены, аудит обезличен, имя можно занять снова."""
    client, _app = itg_client
    admin_headers = await _login(client, "admin")
    user = await _create_user(client, admin_headers, "qa_hard")
    user_id = uuid.UUID(user["user_id"])
    username = user["username"]

    user_headers = await _login(client, username, "qalife1234")  # создаёт auth-сессию

    async with db_factory() as session:
        chat = ChatSession(id=uuid.uuid4(), user_id=user_id, title="to-be-removed")
        session.add(chat)
        await session.flush()
        session.add(
            ChatMessage(
                id=uuid.uuid4(), chat_session_id=chat.id, role="user", content="привет"
            )
        )
        session.add(AuditLog(action="test.event", user_id=user_id))
        await session.commit()
        audit_ids = (
            (
                await session.execute(
                    select(AuditLog.id).where(AuditLog.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(audit_ids) == 2  # admin.user_created + test.event

    deleted = await client.delete(
        f"/admin/users/{user_id}?hard=true", headers=admin_headers
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["mode"] == "deleted"

    async with db_factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(User).where(User.id == user_id))
        ) == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(ChatMessage).join(
                    ChatSession, ChatSession.id == ChatMessage.chat_session_id
                ).where(ChatSession.user_id == user_id)
            )
        ) == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(ChatSession).where(ChatSession.user_id == user_id)
            )
        ) == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(AuthSession).where(AuthSession.user_id == user_id)
            )
        ) == 0
        # события аудита сохранены и обезличены
        for aid in audit_ids:
            row = await session.get(AuditLog, aid)
            assert row is not None and row.user_id is None

    # пользователь больше не может войти; имя освобождено
    assert (
        await client.post("/auth/login", json={"username": username, "password": "qalife1234"})
    ).status_code == 401
    assert (await client.get("/auth/me", headers=user_headers)).status_code == 401
    recreated = await client.post(
        "/admin/users",
        headers=admin_headers,
        json={"username": username, "password": "qalife1234", "role": "viewer"},
    )
    assert recreated.status_code == 201, recreated.text

    # повторное hard-удаление отсутствующего → 404
    assert (
        await client.delete(f"/admin/users/{user_id}?hard=true", headers=admin_headers)
    ).status_code == 404


@pytest.mark.integration
async def test_self_protection_and_last_admin_guard(db_factory, itg_client) -> None:
    """Нельзя удалить/деактивировать себя; guard последнего админа на уровне БД-хелпера."""
    client, _app = itg_client
    admin_headers = await _login(client, "admin")
    admin_id = (await client.get("/auth/me", headers=admin_headers)).json()["user_id"]

    assert (
        await client.delete(f"/admin/users/{admin_id}", headers=admin_headers)
    ).status_code == 403
    assert (
        await client.delete(f"/admin/users/{admin_id}?hard=true", headers=admin_headers)
    ).status_code == 403
    assert (
        await client.patch(
            f"/admin/users/{admin_id}/status", headers=admin_headers, json={"is_active": False}
        )
    ).status_code == 403

    # не-админ не имеет доступа к операциям жизненного цикла
    victim = await _create_user(client, admin_headers, "qa_vic")
    viewer_headers = await _login(client, victim["username"], "qalife1234")
    assert (
        await client.delete(f"/admin/users/{victim['user_id']}", headers=viewer_headers)
    ).status_code == 403
    assert (
        await client.delete(f"/admin/users/{victim['user_id']}?hard=true", headers=viewer_headers)
    ).status_code == 403

    # единственный активный админ в БД → другой активный админ не существует
    async with db_factory() as session:
        admin_row = (
            await session.execute(select(User).where(User.username == "admin"))
        ).scalar_one()
        assert admin_row.role.name == RoleName.ADMIN
        assert not await other_active_admin_exists(session, admin_row.id)

        extra = User(
            username=f"adm_{uuid.uuid4().hex[:6]}",
            password_hash="x",
            role_id=admin_row.role_id,
        )
        session.add(extra)
        await session.flush()
        assert await other_active_admin_exists(session, admin_row.id)

        extra.is_active = False
        await session.flush()
        assert not await other_active_admin_exists(session, admin_row.id)
