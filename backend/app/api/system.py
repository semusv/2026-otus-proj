"""Системные эндпоинты: health и metrics."""

from fastapi import APIRouter, Response

router = APIRouter(tags=["system"])

_VERSION = "0.3.0"


@router.get("/health", summary="Проверка живости сервиса")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "backend", "version": _VERSION}


@router.get(
    "/metrics",
    summary="Метрики Prometheus",
    description="Базовый эндпоинт; полные метрики этапа 7 (ADR-008).",
)
def metrics() -> Response:
    body = (
        "# HELP app_up Backend availability flag.\n"
        "# TYPE app_up gauge\n"
        "app_up 1\n"
    )
    return Response(content=body, media_type="text/plain; version=0.0.4")


def get_version() -> str:
    return _VERSION
