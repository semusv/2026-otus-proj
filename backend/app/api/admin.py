"""Admin API: запуск ingestion (background task) и статус прогона.

Только роль admin. Одновременно выполняется не более одного прогона
(гвардится asyncio.Lock + состояние в app.state).
"""

import asyncio
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_current_user
from app.config import Settings
from app.core.errors import ForbiddenError, IngestAlreadyRunningError
from app.db.models import User
from app.ingestion.pipeline import run_ingestion
from app.schemas.admin import IngestStartResponse, IngestStatusResponse

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
    state.update({"state": "running", "started_at": datetime.now(UTC), "finished_at": None,
                  "stats": None, "error": None})

    async def _job() -> None:
        try:
            stats = await run_ingestion(settings, corpus_dir)
            state.update(
                {"state": "done", "finished_at": datetime.now(UTC), "stats": asdict(stats)}
            )
        except Exception as exc:  # статус ошибки виден через /ingest/status
            logger.exception("Ingestion упал")
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
    return IngestStatusResponse.model_validate(_state(request.app.state))
