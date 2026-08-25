"""Pydantic-схемы auth-эндпоинтов (контракт API)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """Тело запроса логина."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"username": "analyst", "password": "analyst123"}]}
    )

    username: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_.-]+$",
        description="Имя пользователя",
    )
    password: str = Field(min_length=8, max_length=72, description="Пароль (bcrypt на сервере)")


class TokenResponse(BaseModel):
    """Успешный логин: JWT + метаданные сессии."""

    model_config = ConfigDict(json_schema_extra={"examples": [
        {
            "access_token": "eyJhbGciOiJIUzI1NiIs...",
            "token_type": "bearer",
            "expires_in": 1800,
            "username": "analyst",
            "role": "analyst",
        }
    ]})

    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 — тип токена, не пароль
    expires_in: int = Field(description="TTL токена, секунды")
    username: str
    role: str


class MeResponse(BaseModel):
    """Профиль текущего пользователя (по Bearer-токену)."""

    user_id: str
    username: str
    role: str
    clearances: list[str] = Field(description="Метки доступа роли (ACL для pre-fetch фильтров)")
