"""Операции с учётными записями: создание пользователя (общее для register и admin API)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UserExistsError
from app.core.security import hash_password
from app.db.models import Role, RoleName, User


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
