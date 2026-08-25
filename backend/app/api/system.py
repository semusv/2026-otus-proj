"""Системные эндпоинты: health и metrics."""

from fastapi import APIRouter, Response

from app.observability.metrics import render_metrics

router = APIRouter(tags=["system"])

_VERSION = "0.6.0"


@router.get("/health", summary="Проверка живости сервиса")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "backend", "version": _VERSION}


@router.get(
    "/metrics",
    summary="Метрики Prometheus",
    description="HTTP RPS/latency по маршрутам + бизнес-метрики чата/графа (этап 7, ADR-008).",
)
def metrics() -> Response:
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


def get_version() -> str:
    return _VERSION
