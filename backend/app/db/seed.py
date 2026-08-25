"""Сидинг: роли (идемпотентно) и 3 демо-пользователя viewer/analyst/admin."""

import logging
from collections.abc import Mapping

from sqlalchemy import select

from app.config import Settings
from app.core.security import hash_password
from app.db.base import Database
from app.db.models import Role, RoleName, User

logger = logging.getLogger("app.seed")

DEFAULT_PASSWORDS: dict[str, str] = {
    "viewer": "viewer123",
    "analyst": "analyst123",
    "admin": "admin123",
}

ROLE_BY_USERNAME: dict[str, RoleName] = {
    "viewer": RoleName.VIEWER,
    "analyst": RoleName.ANALYST,
    "admin": RoleName.ADMIN,
}


async def seed(
    settings: Settings,
    passwords: Mapping[str, str] | None = None,
) -> dict[str, list[str]]:
    """Создаёт отсутствующие роли и пользователей; существующих обновляет (пароль/роль)."""
    pwd = {**DEFAULT_PASSWORDS, **(passwords or {})}
    db = Database(settings)
    try:
        async with db.session_factory() as session:
            roles: dict[RoleName, Role] = {}
            for rn in RoleName:
                role = (
                    await session.execute(select(Role).where(Role.name == rn))
                ).scalar_one_or_none()
                if role is None:
                    role = Role(name=rn)
                    session.add(role)
                roles[rn] = role
            await session.flush()

            created: list[str] = []
            updated: list[str] = []
            for username, user_role in ROLE_BY_USERNAME.items():
                user = (
                    await session.execute(select(User).where(User.username == username))
                ).scalar_one_or_none()
                new_hash = hash_password(pwd[username])
                if user is None:
                    user = User(
                        username=username, password_hash=new_hash, role_id=roles[user_role].id
                    )
                    session.add(user)
                    created.append(username)
                else:
                    user.password_hash = new_hash
                    user.role_id = roles[user_role].id
                    updated.append(username)
            await session.commit()
        logger.info("Сид завершён: созданы=%s, обновлены=%s", created, updated)
        return {"created": created, "updated": updated}
    finally:
        await db.dispose()
