"""Операции с учётными записями: создание, деактивация, жёсткое удаление.

Порядок чистки при hard delete (см. бэклог п.6 и scripts/cleanup_test_users.sql):
chat_messages → chat_sessions → sessions (auth) → audit_log.user_id=NULL → users.
Qdrant/Neo4j не затрагиваются (пользовательские данные не ссылаются на корпус).
"""

import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UserExistsError
from app.core.security import hash_password
from app.db.models import AuditLog, AuthSession, ChatMessage, ChatSession, Role, RoleName, User


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    return (
        await session.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    role_name: RoleName,
) -> User:
    """Создаёт пользователя с хешированным паролем; 409 при занятом имени."""
    existing = await get_user_by_username(session, username)
    if existing is not None:
        raise UserExistsError()

    role = (await session.execute(select(Role).where(Role.name == role_name))).scalar_one()
    if role is None:
        raise LookupError(f"Роль не найдена: {role_name}")

    user = User(
        username=username,
        password_hash=hash_password(password),
        role_id=role.id,
    )
    session.add(user)
    await session.flush()  # id/created_at до сборки ответа
    return user


async def other_active_admin_exists(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """Есть ли ДРУГОЙ активный админ помимо user_id (защита последнего админа)."""
    count = await session.scalar(
        select(func.count())
        .select_from(User)
        .join(Role, Role.id == User.role_id)
        .where(Role.name == RoleName.ADMIN, User.is_active.is_(True), User.id != user_id)
    )
    return int(count or 0) > 0


async def delete_user_cascade(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Жёстко удаляет пользователя и его диалоги/сессии; аудит обезличивается.

    Возвращает число удалённых сообщений чата (для аудита/логов). История
    переписки удаляется вместе с диалогами; события audit_log сохраняются
    с user_id=NULL (обезличенные).
    """
    chat_session_ids = select(ChatSession.id).where(ChatSession.user_id == user_id)

    messages_removed = int(
        await session.scalar(
            select(func.count())
            .select_from(ChatMessage)
            .where(ChatMessage.chat_session_id.in_(chat_session_ids))
        )
        or 0
    )
    await session.execute(delete(ChatMessage).where(ChatMessage.chat_session_id.in_(chat_session_ids)))
    await session.execute(delete(ChatSession).where(ChatSession.user_id == user_id))
    await session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
    await session.execute(
        update(AuditLog).where(AuditLog.user_id == user_id).values(user_id=None)
    )
    await session.execute(delete(User).where(User.id == user_id))
    await session.flush()
    return messages_removed
