"""Схемы ошибок API (единый контракт {code, message[, details]})."""

from typing import Any

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Тело ошибки домена/валидации."""

    code: str
    message: str
    details: list[dict[str, Any]] | None = None
