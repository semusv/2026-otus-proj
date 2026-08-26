"""Admin API: запуск ingestion (background task) и статус прогона.

Только роль admin. Одновременно выполняется не более одного прогона
(гвардится asyncio.Lock + состояние в app.state).
"""

import asyncio
import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qmodels
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.api.deps import audit_context, get_current_user
from app.config import Settings
from app.core.errors import (
    ActNotFoundError,
    ForbiddenError,
    IngestAlreadyRunningError,
    LastAdminError,
    UserNotFoundError,
)
from app.core.security import resolve_clearances
from app.db.base import get_session
from app.db.models import AuditLog, ChatMessage, ChatSession, Role, RoleName, User
from app.db.users import (
    create_user,
    delete_user_cascade,
    other_active_admin_exists,
)
from app.ingestion.pipeline import IngestProgress, run_ingestion
from app.schemas.acts import ActClearanceUpdate, ActOut
from app.schemas.admin import (
    DeleteUserResponse,
    IngestStartResponse,
    IngestStatusResponse,
    Neo4jStats,
    PostgresStats,
    QdrantStats,
    StorageStatsResponse,
    UserCreate,
    UserOut,
    UserRoleUpdate,
    UsersListResponse,
    UserStatusUpdate,
)

logger = logging.getLogger("app.api.admin")

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(user: User) -> None:
    if user.role.name != "admin":
        raise ForbiddenError("Операция доступна только роли admin")


def _state(app_state: object) -> dict[str, object]:
    if not hasattr(app_state, "ingest_status"):
        app_state.ingest_status = {"state": "idle"}  # type: ignore[attr-defined]
    return app_state.ingest_status  # type: ignore[attr-defined]


@router.post(
    "/ingest",
    response_model=IngestStartResponse,
    status_code=202,
    summary="Запустить ingestion корпуса (background task)",
    responses={403: {"description": "Не admin"}, 409: {"description": "Прогон уже идёт"}},
)
async def start_ingest(
    request: Request, user: User = Depends(get_current_user)
) -> IngestStartResponse:
    _require_admin(user)
    state = _state(request.app.state)
    if state["state"] == "running":
        raise IngestAlreadyRunningError()

    settings: Settings = request.app.state.settings
    corpus_dir = Path(settings.ingest_corpus_dir)
    progress = IngestProgress()
    state.update(
        {
            "state": "running",
            "started_at": datetime.now(UTC),
            "finished_at": None,
            "stats": None,
            "error": None,
            # живой прогресс: объект читается в ingest_status (мутируется job'ом)
            "_progress": progress,
        }
    )

    async def _job() -> None:
        try:
            stats = await run_ingestion(settings, corpus_dir, progress=progress)
            state.update(
                {"state": "done", "finished_at": datetime.now(UTC), "stats": asdict(stats)}
            )
        except Exception as exc:  # статус ошибки виден через /ingest/status
            logger.exception("Ingestion упал")
            progress.stage = "error"
            state.update({"state": "error", "finished_at": datetime.now(UTC), "error": str(exc)})

    request.app.state.ingest_task = asyncio.create_task(_job())
    return IngestStartResponse()


@router.get(
    "/ingest/status",
    response_model=IngestStatusResponse,
    summary="Статус последнего/текущего прогона ingestion",
    responses={403: {"description": "Не admin"}},
)
async def ingest_status(
    request: Request, user: User = Depends(get_current_user)
) -> IngestStatusResponse:
    _require_admin(user)
    state = dict(_state(request.app.state))
    progress = state.pop("_progress", None)
    if isinstance(progress, IngestProgress):
        state.update(
            stage=progress.stage,
            files_done=progress.files_done,
            files_total=progress.files_total,
            chunks_done=progress.chunks_done,
        )
    return IngestStatusResponse.model_validate(state)


@router.get(
    "/stats",
    response_model=StorageStatsResponse,
    summary="Текущее наполнение хранилищ: Qdrant, Neo4j, PostgreSQL (агрегаты)",
    responses={403: {"description": "Не admin"}},
)
async def storage_stats(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> StorageStatsResponse:
    _require_admin(user)

    settings: Settings = request.app.state.settings
    qdrant_client: AsyncQdrantClient = request.app.state.qdrant_client
    collection_info = await qdrant_client.get_collection(settings.qdrant_collection)

    graph_retriever = request.app.state.graph_retriever
    neo4j_counts = await graph_retriever.stats()

    users_count = int(await session.scalar(select(func.count()).select_from(User)) or 0)
    sessions_count = int(await session.scalar(select(func.count()).select_from(ChatSession)) or 0)
    messages_count = int(await session.scalar(select(func.count()).select_from(ChatMessage)) or 0)

    return StorageStatsResponse(
        qdrant=QdrantStats(
            collection=settings.qdrant_collection,
            points=int(collection_info.points_count or 0),
        ),
        neo4j=Neo4jStats(**neo4j_counts),
        postgres=PostgresStats(
            users=users_count,
            chat_sessions=sessions_count,
            chat_messages=messages_count,
        ),
    )


def _user_out(user: User) -> UserOut:
    return UserOut(
        user_id=str(user.id),
        username=user.username,
        role=user.role.name.value,
        clearances=resolve_clearances(user.role.name),
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.get(
    "/users",
    response_model=UsersListResponse,
    summary="Список учётных записей (без секретов)",
    responses={403: {"description": "Не admin"}},
)
async def list_users(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> UsersListResponse:
    _require_admin(user)
    rows = (
        (
            await session.execute(
                select(User).options(joinedload(User.role)).order_by(User.created_at)
            )
        )
        .scalars()
        .all()
    )
    return UsersListResponse(users=[_user_out(u) for u in rows])


@router.post(
    "/users",
    response_model=UserOut,
    status_code=201,
    summary="Создать учётную запись с указанной ролью",
    responses={403: {"description": "Не admin"}, 409: {"description": "Имя занято"}},
)
async def add_user(
    payload: UserCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> UserOut:
    """Роль задаёт метки доступа: viewer→PUBLIC, analyst→PUBLIC+INTERNAL, admin→все."""
    _require_admin(user)
    created = await create_user(
        session,
        username=payload.username,
        password=payload.password,
        role_name=RoleName(payload.role),
    )
    session.add(
        AuditLog(
            action="admin.user_created",
            user_id=user.id,
            detail={"target": created.username, "role": payload.role},
            **audit_context(),
        )
    )
    await session.commit()
    await session.refresh(created, attribute_names=["role"])
    return _user_out(created)


@router.patch(
    "/users/{user_id}",
    response_model=UserOut,
    summary="Сменить роль пользователя (метки доступа пересчитаются из роли)",
    responses={
        403: {"description": "Не admin или попытка сменить собственную роль"},
        404: {"description": "Пользователь не найден"},
    },
)
async def change_user_role(
    user_id: uuid.UUID,
    payload: UserRoleUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> UserOut:
    """Права применяются сразу: clearances резолвятся из БД на каждом запросе,
    повторный вход не требуется. Смена собственной роли запрещена (защита от
    случайной потери последнего админа)."""
    _require_admin(user)
    if user.id == user_id:
        raise ForbiddenError("Нельзя изменить собственную роль")

    target = (
        await session.execute(select(User).options(joinedload(User.role)).where(User.id == user_id))
    ).scalar_one_or_none()
    if target is None:
        raise UserNotFoundError()

    new_role = (
        await session.execute(select(Role).where(Role.name == RoleName(payload.role)))
    ).scalar_one()
    old_role = target.role.name.value
    target.role_id = new_role.id
    session.add(
        AuditLog(
            action="admin.user_role_changed",
            user_id=user.id,
            detail={"target": target.username, "old_role": old_role, "new_role": payload.role},
            **audit_context(),
        )
    )
    await session.commit()
    await session.refresh(target, attribute_names=["role"])
    return _user_out(target)


async def _load_target(
    session: AsyncSession, user_id: uuid.UUID
) -> User:
    target = (
        await session.execute(select(User).options(joinedload(User.role)).where(User.id == user_id))
    ).scalar_one_or_none()
    if target is None:
        raise UserNotFoundError()
    return target


async def _ensure_not_last_admin(session: AsyncSession, target: User) -> None:
    """Блокирует деактивацию/удаление последнего активного админа."""
    if (
        target.role.name == RoleName.ADMIN
        and target.is_active
        and not await other_active_admin_exists(session, target.id)
    ):
        raise LastAdminError()


@router.patch(
    "/users/{user_id}/status",
    response_model=UserOut,
    summary="Деактивировать/реактивировать учётную запись (мягкое отключение)",
    responses={
        403: {"description": "Не admin или попытка изменить себя"},
        404: {"description": "Пользователь не найден"},
        409: {"description": "Последний активный админ"},
    },
)
async def set_user_status(
    user_id: uuid.UUID,
    payload: UserStatusUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> UserOut:
    """Мягкое отключение: is_active=false блокирует вход (login → 401) и
    инвалидирует существующие JWT (любой запрос → 401), история и аудит целы.
    Реактивация возвращает доступ без создания новых сущностей."""
    _require_admin(user)
    if user.id == user_id:
        raise ForbiddenError("Нельзя изменить статус собственной учётной записи")

    target = await _load_target(session, user_id)
    if payload.is_active is False:
        await _ensure_not_last_admin(session, target)

    old_state = target.is_active
    target.is_active = payload.is_active
    session.add(
        AuditLog(
            action="admin.user_status_changed",
            user_id=user.id,
            detail={
                "target": target.username,
                "old_is_active": old_state,
                "new_is_active": payload.is_active,
            },
            **audit_context(),
        )
    )
    await session.commit()
    await session.refresh(target, attribute_names=["role"])
    return _user_out(target)


@router.delete(
    "/users/{user_id}",
    response_model=DeleteUserResponse,
    summary="Удалить учётную запись (по умолчанию мягко — деактивация; ?hard=true — физически)",
    responses={
        403: {"description": "Не admin или попытка удалить себя"},
        404: {"description": "Пользователь не найден"},
        409: {"description": "Последний активный админ"},
    },
)
async def delete_user(
    user_id: uuid.UUID,
    hard: bool = False,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> DeleteUserResponse:
    """hard=false (дефолт): деактивация — вход заблокирован, история/аудит целы.
    hard=true: физическое удаление строки users c чисткой FK-зависимостей
    (chat_messages → chat_sessions → sessions) и обезличиванием audit_log
    (user_id=NULL, события сохраняются). Qdrant/Neo4j не затрагиваются.
    Защиты: нельзя удалить себя; нельзя удалить последнего активного админа.
    Имя освобождается только при hard=true (при soft имя остаётся занятым)."""
    _require_admin(user)
    if user.id == user_id:
        raise ForbiddenError("Нельзя удалить собственную учётную запись")

    target = await _load_target(session, user_id)
    username = target.username

    if hard:
        await _ensure_not_last_admin(session, target)
        messages = await delete_user_cascade(session, target.id)
        session.add(
            AuditLog(
                action="admin.user_deleted",
                user_id=user.id,
                detail={"target": username, "mode": "hard", "chat_messages_removed": messages},
                **audit_context(),
            )
        )
        await session.commit()
        return DeleteUserResponse(user_id=str(user_id), username=username, mode="deleted")

    await _ensure_not_last_admin(session, target)
    if target.is_active:
        target.is_active = False
        session.add(
            AuditLog(
                action="admin.user_deleted",
                user_id=user.id,
                detail={"target": username, "mode": "soft_deactivate"},
                **audit_context(),
            )
        )
        await session.commit()
    return DeleteUserResponse(user_id=str(user_id), username=username, mode="deactivated")


@router.patch(
    "/acts/{act_id}/clearance",
    response_model=ActOut,
    summary="Перекатегоризация акта: сменить гриф (Neo4j + payload чанков Qdrant)",
    responses={
        403: {"description": "Не admin"},
        404: {"description": "Акт не найден"},
        422: {"description": "Неизвестная метка доступа"},
    },
)
async def change_act_clearance(
    act_id: str,
    payload: ActClearanceUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ActOut:
    """Ручная перекатегоризация (бэклог п.4). Метка меняется синхронно в двух
    хранилищах: Neo4j Act.clearance (метаданные) и payload всех Qdrant-точек
    акта - pre-fetch ACL ретриверов читает метку именно из payload. Доступ
    пересчитывается мгновенно, перезапуск/переиндексация не нужны."""
    _require_admin(user)

    settings: Settings = request.app.state.settings
    graph_retriever = request.app.state.graph_retriever

    row = await graph_retriever.set_act_clearance(act_id, payload.clearance)
    if row is None:
        raise ActNotFoundError()

    await request.app.state.qdrant_client.set_payload(
        collection_name=settings.qdrant_collection,
        payload={"clearance": payload.clearance},
        points_selector=qmodels.Filter(
            must=[qmodels.FieldCondition(key="act_id", match=qmodels.MatchAny(any=[act_id]))]
        ),
        wait=True,
    )
    session.add(
        AuditLog(
            action="admin.act_clearance_changed",
            user_id=user.id,
            detail={"act_id": act_id, "clearance": payload.clearance},
            **audit_context(),
        )
    )
    await session.commit()

    return ActOut(
        act_id=str(row.get("id", "")),
        title=str(row.get("title") or ""),
        doc_number=row.get("doc_number") or None,
        date=row.get("date") or None,
        status=row.get("status") or None,
        clearance=payload.clearance,
    )
