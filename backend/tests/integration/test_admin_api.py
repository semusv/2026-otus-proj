"""Интеграционные тесты admin/ingest API (pipeline мокается - без LLM/БД нагрузки)."""

from pathlib import Path
from typing import Any

import pytest
from app.config import Settings
from app.ingestion.pipeline import IngestStats

from tests.integration.helpers import CREDENTIALS, seed_users

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def seeded_users(base_settings: Settings, pg_dsn: str) -> None:
    await seed_users(base_settings, pg_dsn)


@pytest.fixture(autouse=True)
def fake_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Мокаем тяжёлый прогон: эндпоинты проверяем на уровне контракта."""

    class _FakeTask:
        def __init__(self, coro: Any) -> None:
            self._coro = coro

        def done(self) -> bool:
            return True

    async def _fake_run(settings: Settings, corpus_dir: Path, **kwargs: Any) -> IngestStats:
        return IngestStats(files_total=3, acts_parsed=3, chunks_written=42)

    import app.api.admin as admin_module

    monkeypatch.setattr(admin_module, "run_ingestion", _fake_run)


async def _login(client: Any, username: str) -> str:
    resp = await client.post(
        "/auth/login", json={"username": username, "password": CREDENTIALS[username]}
    )
    return resp.json()["access_token"]


class TestAdminIngestApi:
    async def test_start_requires_admin(
        self, itg_client: tuple[Any, Any]
    ) -> None:
        client, _ = itg_client
        token = await _login(client, "viewer")
        resp = await client.post("/admin/ingest", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403
        assert resp.json()["code"] == "forbidden"

    async def test_status_requires_admin(self, itg_client: tuple[Any, Any]) -> None:
        client, _ = itg_client
        token = await _login(client, "viewer")
        resp = await client.get(
            "/admin/ingest/status", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403

    async def test_start_and_status_roundtrip(
        self, itg_client: tuple[Any, Any]
    ) -> None:
        client, _ = itg_client
        token = await _login(client, "admin")

        started = await client.post("/admin/ingest", headers={"Authorization": f"Bearer {token}"})
        assert started.status_code == 202
        assert started.json()["state"] == "started"

        # фейковая задача завершена мгновенно -> статус уже не running
        status = await client.get(
            "/admin/ingest/status", headers={"Authorization": f"Bearer {token}"
        })
        assert status.status_code == 200
        body = status.json()
        assert body["state"] in {"running", "done"}
        if body["state"] == "done":
            assert body["stats"]["chunks_written"] == 42

    async def test_second_start_conflict_or_accepted(
        self, itg_client: tuple[Any, Any]
    ) -> None:
        client, _ = itg_client
        token = await _login(client, "admin")
        await client.post("/admin/ingest", headers={"Authorization": f"Bearer {token}"})
        second = await client.post("/admin/ingest", headers={"Authorization": f"Bearer {token}"})
        assert second.status_code in {202, 409}
