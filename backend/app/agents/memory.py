"""Memory module агента (по заданию): история диалога живёт в PostgreSQL.

Состояние графа остаётся лёгким: в него подгружаются только последние
APP_CHAT_HISTORY_LIMIT сообщений сессии (ADR-005 - состояние сериализуемо,
источник истины - БД).
"""

import uuid

from sqlalchemy import select

from app.core.errors import ForbiddenError
from app.db.base import Database
from app.db.models import ChatMessage, ChatSession, User


class ChatMemory:
    """Загрузка истории и сохранение ходов диалога."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def ensure_session(
        self,
        user: User,
        session_id: uuid.UUID | None,
        *,
        title: str | None = None,
    ) -> ChatSession:
        """Возвращает существующую сессию пользователя или создаёт новую."""
        async with self._db.session_factory() as session:
            if session_id is not None:
                chat_session = await session.get(ChatSession, session_id)
                if chat_session is not None:
                    if chat_session.user_id != user.id:
                        raise ForbiddenError("Сессия диалога принадлежит другому пользователю")
                    return chat_session

            chat_session = ChatSession(id=uuid.uuid4(), user_id=user.id, title=title)
            session.add(chat_session)
            await session.commit()
            await session.refresh(chat_session)
            return chat_session

    async def load_history(self, chat_session_id: uuid.UUID, limit: int) -> list[dict[str, str]]:
        """Последние ``limit`` сообщений сессии в хронологическом порядке."""
        if limit <= 0:
            return []
        async with self._db.session_factory() as session:
            result = await session.execute(
                select(ChatMessage.role, ChatMessage.content)
                .where(ChatMessage.chat_session_id == chat_session_id)
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(limit)
            )
            rows = result.all()
        return [{"role": role, "content": content} for role, content in reversed(rows)]

    async def save_turn(
        self,
        chat_session_id: uuid.UUID,
        *,
        question: str,
        answer: str,
        citations: list[dict[str, object]],
        trace_id: str | None,
    ) -> None:
        """Сохраняет пару сообщений user/assistant одним коммитом."""
        async with self._db.session_factory() as session:
            session.add_all(
                [
                    ChatMessage(
                        chat_session_id=chat_session_id,
                        role="user",
                        content=question,
                        trace_id=trace_id,
                    ),
                    ChatMessage(
                        chat_session_id=chat_session_id,
                        role="assistant",
                        content=answer,
                        sources=citations,
                        trace_id=trace_id,
                    ),
                ]
            )
            await session.commit()
