"""Схемы просмотра актов и управления грифом (бэклог п.4/п.5)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

ClearanceLiteral = Literal["PUBLIC", "INTERNAL", "SECRET"]


class ActOut(BaseModel):
    """Метаданные акта из графа знаний."""

    act_id: str
    title: str
    doc_number: str | None = None
    date: str | None = None
    status: str | None = None
    clearance: ClearanceLiteral


class ActContentResponse(BaseModel):
    """Полный текст акта, собранный из чанков векторного хранилища.

    Выдаётся только если метка акта входит в ACL пользователя (бэклог п.5).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "act": {
                        "act_id": "920000001",
                        "title": "Об акционерных обществах",
                        "doc_number": "920000001-ФЗ",
                        "date": "15.03.2001",
                        "status": "Действует без изменений",
                        "clearance": "PUBLIC",
                    },
                    "chunk_count": 1,
                    "full_text": "Статья 1. ...",
                }
            ]
        }
    )

    act: ActOut
    chunk_count: int
    full_text: str


class ActClearanceUpdate(BaseModel):
    """Ручная перекатегоризация акта админом (бэклог п.4).

    Обновляет метку синхронно в обоих хранилищах: Neo4j (метаданные) и Qdrant
    (payload всех чанков - фильтр pre-fetch ACL читает метку именно оттуда).
    """

    model_config = ConfigDict(json_schema_extra={"examples": [{"clearance": "SECRET"}]})

    clearance: ClearanceLiteral
