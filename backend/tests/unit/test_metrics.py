"""Unit-тесты метрик этапа 7: /metrics отдаёт prometheus-текст, /health не скрейпится."""

import pytest
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def client(settings) -> TestClient:  # type: ignore[no-untyped-def]
    app: FastAPI = create_app(settings)
    return TestClient(app)


def test_metrics_endpoint_exposes_prometheus_text(settings) -> None:  # type: ignore[no-untyped-def]
    c = client(settings)
    health = c.get("/health")
    assert health.status_code == 200

    resp = c.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    # семейства метрик этапа 7 присутствуют
    for family in (
        "http_requests_total",
        "http_request_duration_seconds",
        "chat_status_total",
        "guardrail_events_total",
        "graph_node_duration_seconds",
        "agent_iterations",
        "rag_context_chunks",
    ):
        assert family in body, family


def test_health_and_metrics_excluded_from_http_series(settings) -> None:  # type: ignore[no-untyped-def]
    c = client(settings)
    c.get("/health")
    c.get("/metrics")
    body = c.get("/metrics").text
    assert 'route="/health"' not in body
    assert 'route="/metrics"' not in body


def test_request_updates_route_series(settings) -> None:  # type: ignore[no-untyped-def]
    c = client(settings)
    # несматченный путь -> route="unmatched", статус 404 попадает в счётчик
    c.get("/definitely-not-a-route")
    body = c.get("/metrics").text
    line = next(
        (
            ln
            for ln in body.splitlines()
            if ln.startswith("http_requests_total{") and 'route="unmatched"' in ln
        ),
        None,
    )
    assert line is not None
    assert 'status="404"' in line
