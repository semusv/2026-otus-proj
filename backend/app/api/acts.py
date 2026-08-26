"""API актов: полный текст источника по цитате (бэклог п.5).

ACL повторяет pre-fetch правило чанков: метка акта обязана входить в набор
разрешений роли (viewer=PUBLIC, analyst=+INTERNAL, admin=все). Текст собирается
из чанков Qdrant (payload хранит chunk_no/clearance), мета - из Neo4j.
"""

from fastapi import APIRouter, Depends, Request
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qmodels

from app.api.deps import get_current_user
from app.config import Settings
from app.core.errors import ActNotFoundError, ForbiddenError
from app.core.security import resolve_clearances
from app.db.models import User
from app.schemas.acts import ActContentResponse, ActOut

router = APIRouter(prefix="/api/acts", tags=["acts"])


async def _fetch_act_chunks(
    client: AsyncQdrantClient, collection: str, act_id: str
) -> list[tuple[int, str]]:
    """Все чанки акта из Qdrant, отсортированные по chunk_no."""
    points: list[qmodels.Record] = []
    offset: qmodels.ExtendedPointId | None = None
    flt = qmodels.Filter(
        must=[qmodels.FieldCondition(key="act_id", match=qmodels.MatchAny(any=[act_id]))]
    )
    while True:
        batch, offset = await client.scroll(
            collection_name=collection,
            scroll_filter=flt,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(batch)
        if offset is None:
            break
    return sorted(
        (int((p.payload or {}).get("chunk_no", 0)), str((p.payload or {}).get("text", "")))
        for p in points
    )


def _act_out(row: dict[str, object]) -> ActOut:
    return ActOut(
        act_id=str(row.get("id", "")),
        title=str(row.get("title") or ""),
        doc_number=row.get("doc_number") or None,
        date=row.get("date") or None,
        status=row.get("status") or None,
        clearance=row.get("clearance"),
    )


@router.get(
    "/{act_id}/content",
    response_model=ActContentResponse,
    summary="Полный текст акта с метаданными (доступ по метке грифа)",
    responses={
        403: {"description": "Метка акта выше уровня доступа роли"},
        404: {"description": "Акт не найден"},
    },
)
async def act_content(
    act_id: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> ActContentResponse:
    settings: Settings = request.app.state.settings
    graph_retriever = request.app.state.graph_retriever

    row = await graph_retriever.get_act(act_id)
    if row is None:
        raise ActNotFoundError()

    clearance = str(row.get("clearance") or "PUBLIC")
    if clearance not in resolve_clearances(user.role.name):
        raise ForbiddenError(
            f"Метка акта {clearance} не входит в ваш уровень доступа"
        )

    chunks = await _fetch_act_chunks(
        request.app.state.qdrant_client, settings.qdrant_collection, act_id
    )

    return ActContentResponse(
        act=_act_out(row),
        chunk_count=len(chunks),
        full_text="\n\n".join(text for _, text in chunks),
    )
