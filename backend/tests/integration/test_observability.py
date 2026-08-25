"""Интеграционные тесты observability этапа 7.

1. Метрики: /auth/login создаёт серию http_requests_total по route-template.
2. Логи: JSON-запись app.access содержит user_id/trace_id/request_id
   (хендлер вешается на ИСХОДНЫЙ логгер - configure_logging чистит root.handlers).
3. Трейсы (живой стек): X-Trace-Id -> трейс в Jaeger API; skip если стек не поднят.

Требует запущенный compose. Запуск: make test-integration (--timeout=300 из Makefile).
"""

import asyncio
import json
import logging
import re

import httpx
import pytest

from tests.integration.helpers import CREDENTIALS, seed_users

pytestmark = pytest.mark.integration

HEX32 = re.compile(r"^[0-9a-f]{32}$")
# httpcore резолвит *.localhost в ::1, а порты забинжены на 127.0.0.1 ->
# ходим по IP с явным Host-заголовком (Traefik маршрутизирует по нему)
JAEGER_HOST = "jaeger.localhost"
API_HOST = "api.localhost"
LOCAL = "http://127.0.0.1"


def _host_headers(host: str) -> dict[str, str]:
    return {"Host": host}


@pytest.fixture(autouse=True)
async def _seed(base_settings, pg_dsn) -> None:
    await seed_users(base_settings, pg_dsn)


async def test_metrics_series_updated_after_request(itg_client) -> None:
    client, _ = itg_client
    login = await client.post(
        "/auth/login", json={"username": "nouser", "password": "wrongpass"}
    )
    assert login.status_code == 401

    metrics = await client.get("/metrics")
    assert metrics.status_code == 200
    # в полном прогоне у маршрута уже есть серии других статусов - ищем свою пару
    target = 'route="/auth/login",status="401"'
    assert any(
        ln.startswith("http_requests_total{") and target in ln
        for ln in metrics.text.splitlines()
    ), f"нет серии http_requests_total для {target}"


def _grab_source_logger(name: str) -> tuple[logging.Handler, list[logging.LogRecord], callable]:  # type: ignore[type-arg]
    records: list[logging.LogRecord] = []

    class _Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Grab(level=logging.INFO)
    logging.getLogger(name).addHandler(handler)
    return handler, records, lambda: logging.getLogger(name).removeHandler(handler)


async def test_logs_contain_user_id_after_auth(itg_client) -> None:
    client, app = itg_client
    _ = app
    # НЕ caplog и НЕ root: create_app вызывает root.handlers.clear();
    # вешаем хендлер на логгер источника - он срабатывает до propagation.
    access_records: list[logging.LogRecord] = []

    class _Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            access_records.append(record)

    grab = _Grab(level=logging.INFO)
    src = logging.getLogger("app.access")
    src.addHandler(grab)
    try:
        login = await client.post(
            "/auth/login",
            json={"username": "viewer", "password": CREDENTIALS["viewer"]},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]

        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        expected_uid = str(me.json()["user_id"])
    finally:
        src.removeHandler(grab)

    assert access_records, (
        f"нет access-логов; disabled={src.disabled}; "
        f"eff={logging.getLevelName(src.getEffectiveLevel())}"
    )
    user_ids = {getattr(r, "user_id", "-") for r in access_records}
    assert expected_uid in user_ids, f"user_id {expected_uid} не в логах: {user_ids}"

    echo_trace = me.headers.get("x-trace-id", "")
    assert HEX32.match(echo_trace)
    traced = [getattr(r, "trace_id", "") for r in access_records]
    assert echo_trace in traced, "trace_id лога != X-Trace-Id из ответа"

    from app.core.logging import JsonFormatter

    payload = json.loads(JsonFormatter().format(access_records[-1]))
    assert payload["user_id"] == expected_uid
    assert payload["trace_id"] == echo_trace
    assert payload["request_id"]


@pytest.mark.timeout(60)
async def test_jaeger_trace_found_by_x_trace_id() -> None:
    """E2E против живого стека: X-Trace-Id -> трейс в Jaeger (Traefik -> collector).

    Пропуск, если observability-стек не поднят (make test-integration не падает).
    """
    probe_client = httpx.AsyncClient(trust_env=False, timeout=5)
    try:
        try:
            probe = await asyncio.wait_for(
                probe_client.get(f"{LOCAL}/api/services",
                                 headers=_host_headers(JAEGER_HOST)),
                timeout=5,
            )
            backend_probe = await asyncio.wait_for(
                probe_client.get(f"{LOCAL}/health", headers=_host_headers(API_HOST)),
                timeout=5,
            )
        except Exception:
            pytest.skip("Живой стек (traefik/jaeger/backend) недоступен")
        if probe.status_code != 200 or backend_probe.status_code != 200:
            pytest.skip("Живой стек (traefik/jaeger/backend) недоступен")
    finally:
        await probe_client.aclose()

    trace_id = "7c9f" + "a1b2" * 7  # детерминированный 32-hex
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        resp = await client.get(
            f"{LOCAL}/health",
            headers={**_host_headers(API_HOST), "X-Trace-Id": trace_id,
                     "traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"},
        )
        assert resp.headers["X-Trace-Id"] == trace_id

        found = False
        for _ in range(30):  # BatchSpanProcessor ~5s; до ~45s на экспорт+индексацию
            await asyncio.sleep(1.5)
            r = await client.get(f"{LOCAL}/api/traces/{trace_id}",
                                 headers=_host_headers(JAEGER_HOST))
            if r.status_code == 200 and r.json().get("data"):
                found = True
                span_names = {s["operationName"] for s in r.json()["data"][0]["spans"]}
                assert any(
                    n.startswith("graph.") or n == "http.request" for n in span_names
                ), span_names
                break
        assert found, f"трейс {trace_id} не появился в Jaeger за отведённое время"
