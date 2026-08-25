"""Тесты correlation-middleware: эхо X-Trace-Id, генерация идентификаторов, traceparent."""

import re

import pytest
from app.config import Settings
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

HEX32 = re.compile(r"^[0-9a-f]{32}$")


def client(settings: Settings) -> TestClient:
    app: FastAPI = create_app(settings)
    return TestClient(app)


def test_generated_ids_on_plain_request(settings: Settings) -> None:
    resp = client(settings).get("/health")
    assert resp.status_code == 200
    assert HEX32.match(resp.headers["X-Trace-Id"])
    assert HEX32.match(resp.headers["X-Request-Id"])
    assert resp.headers["X-Trace-Id"] != resp.headers["X-Request-Id"]


def test_trace_id_echoed_valid_hex(settings: Settings) -> None:
    """Валидный 32-hex проходит как есть (этап 7: == trace id трейса)."""
    tid = "4bf92f3577b34da6a3ce929d0e0e4736"
    resp = client(settings).get("/health", headers={"X-Trace-Id": tid})
    assert resp.headers["X-Trace-Id"] == tid


def test_trace_id_arbitrary_normalized_deterministically(settings: Settings) -> None:
    """Произвольная строка хешируется sha256 -> стабильный валидный 32-hex."""
    resp1 = client(settings).get("/health", headers={"X-Trace-Id": "my-trace-123"})
    resp2 = client(settings).get("/health", headers={"X-Trace-Id": "my-trace-123"})
    assert HEX32.match(resp1.headers["X-Trace-Id"])
    assert resp1.headers["X-Trace-Id"] == resp2.headers["X-Trace-Id"]
    assert resp1.headers["X-Trace-Id"] != "my-trace-123"


def test_trace_id_zero_guid_falls_back_to_random(settings: Settings) -> None:
    resp = client(settings).get("/health", headers={"X-Trace-Id": "0" * 32})
    assert HEX32.match(resp.headers["X-Trace-Id"])
    assert resp.headers["X-Trace-Id"] != "0" * 32


def test_trace_id_from_traceparent_fallback(settings: Settings) -> None:
    trace = "4bf92f3577b34da6a3ce929d0e0e4736"
    tp = f"00-{trace}-00f067aa0ba902b7-01"
    resp = client(settings).get("/health", headers={"traceparent": tp})
    assert resp.headers["X-Trace-Id"] == trace


def test_request_id_from_client(settings: Settings) -> None:
    resp = client(settings).get("/health", headers={"X-Request-Id": "req-fixed"})
    assert resp.headers["X-Request-Id"] == "req-fixed"


def test_health_payload_unchanged(settings: Settings) -> None:
    data: dict[str, str] = client(settings).get("/health").json()
    assert data["status"] == "ok"
    assert data["service"] == "backend"
