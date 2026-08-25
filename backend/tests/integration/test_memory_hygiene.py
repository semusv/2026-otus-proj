"""Интеграционные тесты гигиены памяти диалога (этап 9).

Пустой ответ ассистента не должен попадать в историю: assistant="" в промпте
следующих ходов ломает шаблонную генерацию (сервер отдаёт "No user query
found", сессия деградирует по кругу).
"""

import uuid as uuidlib
from datetime import UTC, datetime, timedelta

import pytest
from app.agents.memory import ChatMemory
from app.db.models import ChatMessage
from sqlalchemy import select

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def seeded_users(base_settings, pg_dsn) -> None:
    from tests.integration.helpers import seed_users

    await seed_users(base_settings, pg_dsn)


async def _get_viewer_user(db_factory) -> object:
    from app.db.models import User

    async with db_factory() as session:
        user = (await session.execute(select(User).where(User.username == "viewer"))).scalar_one()
    return user


@pytest.fixture
def memory(itg_client) -> ChatMemory:
    _, app = itg_client
    return ChatMemory(app.state.db)


async def test_empty_answer_is_not_persisted(memory: ChatMemory, db_factory) -> None:
    user = await _get_viewer_user(db_factory)
    chat_session = await memory.ensure_session(user, None, title="t")

    await memory.save_turn(
        chat_session.id,
        question="вопрос без ответа",
        answer="   ",
        citations=[],
        trace_id=None,
    )

    history = await memory.load_history(chat_session.id, 8)
    assert history == []
    async with db_factory() as session:
        count = len(
            (
                await session.execute(
                    select(ChatMessage).where(ChatMessage.chat_session_id == chat_session.id)
                )
            ).all()
        )
    assert count == 0


async def test_load_history_filters_legacy_empty_messages(
    memory: ChatMemory, db_factory
) -> None:
    user = await _get_viewer_user(db_factory)
    chat_session = await memory.ensure_session(user, None, title="legacy")
    sid = chat_session.id

    async with db_factory() as session:
        base_ts = datetime.now(UTC)
        session.add_all(
            [
                ChatMessage(
                    chat_session_id=sid, role="user", content="старый вопрос",
                    created_at=base_ts,
                ),
                ChatMessage(
                    chat_session_id=sid, role="assistant", content="",
                    created_at=base_ts + timedelta(microseconds=1),
                ),
                ChatMessage(
                    chat_session_id=sid, role="user", content="живой вопрос",
                    created_at=base_ts + timedelta(seconds=1),
                ),
                ChatMessage(
                    chat_session_id=sid, role="assistant", content="живой ответ",
                    created_at=base_ts + timedelta(seconds=1, microseconds=1),
                ),
            ]
        )
        await session.commit()

    history = await memory.load_history(sid, 8)
    assert all(msg["content"].strip() for msg in history)
    # пустой assistant отфильтрован, остальное сохраняет хронологию
    assert [msg["content"] for msg in history] == [
        "старый вопрос",
        "живой вопрос",
        "живой ответ",
    ]


async def test_completed_turn_is_saved(memory: ChatMemory, db_factory) -> None:
    user = await _get_viewer_user(db_factory)
    chat_session = await memory.ensure_session(user, None)

    await memory.save_turn(
        chat_session.id,
        question="Вопрос?",
        answer="Ответ с цитатой.",
        citations=[{"source_id": "S1"}],
        trace_id=uuidlib.uuid4().hex,
    )

    history = await memory.load_history(chat_session.id, 8)
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "Вопрос?"),
        ("assistant", "Ответ с цитатой."),
    ]
