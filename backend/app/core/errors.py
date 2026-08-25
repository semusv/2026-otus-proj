"""Доменные ошибки приложения и их отображение в HTTP-ответы."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Базовая ошибка домена: код + человекочитаемое сообщение + HTTP-статус."""

    code: str = "app_error"
    status_code: int = 500
    message: str = "Внутренняя ошибка сервера"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message


class InvalidCredentialsError(AppError):
    code = "invalid_credentials"
    status_code = 401
    message = "Неверный логин или пароль"


class TokenInvalidError(AppError):
    code = "invalid_token"
    status_code = 401
    message = "Токен недействителен"


class TokenExpiredError(AppError):
    code = "token_expired"
    status_code = 401
    message = "Срок действия токена истёк"


def _error_body(
    code: str,
    message: str,
    details: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return body


def setup_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        details: list[dict[str, object]] = [
            {"loc": [str(part) for part in err.get("loc", [])], "msg": str(err.get("msg", ""))}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error_body("validation_error", "Ошибка валидации запроса", details),
        )
