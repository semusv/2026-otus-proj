"""Интеграционные тесты POST /admin/documents (upload XML в корпус).

Каталог корпуса подменяется на tmp_path (реальный corpus_test не трогаем);
pipeline мокается - проверяем контракт, валидацию и запись файлов на диск.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from app.config import Settings

from tests.integration.helpers import CREDENTIALS, seed_users

pytestmark = pytest.mark.integration

XML_BODY = b'<?xml version="1.0" ?><act><body><textIPS>test</textIPS></body></act>'


@pytest.fixture(autouse=True)
async def seeded_users(base_settings: Settings, pg_dsn: str) -> None:
    await seed_users(base_settings, pg_dsn)


@pytest.fixture
async def upload_env(
    itg_client: tuple[Any, Any], tmp_path: Path
) -> AsyncIterator[tuple[Any, Path, Any]]:
    """Клиент с корпусом в tmp_path + доступ к app для подмены состояния."""
    client, app = itg_client
    settings: Settings = app.state.settings
    app.state.settings = settings.model_copy(
        update={"ingest_corpus_dir": tmp_path, "ingest_max_upload_mb": 1}
    )
    yield client, tmp_path, app


async def _login(client: Any, username: str) -> str:
    resp = await client.post(
        "/auth/login", json={"username": username, "password": CREDENTIALS[username]}
    )
    return resp.json()["access_token"]


class TestDocumentsUpload:
    async def test_upload_saves_xml_and_rejects_other(
        self, upload_env: tuple[Any, Path, Any]
    ) -> None:
        client, corpus_dir, _ = upload_env
        token = await _login(client, "admin")
        resp = await client.post(
            "/admin/documents",
            headers={"Authorization": f"Bearer {token}"},
            # multipart list-of-tuples: dict-формат не рендерится httpx2 в multipart
            files=[
                ("files", ("a.xml", XML_BODY, "text/xml")),
                ("files", ("b.xml", XML_BODY, "text/xml")),
                ("files", ("notes.txt", b"not xml", "text/plain")),
            ],
        )
        assert resp.status_code == 201
        body = resp.json()
        assert sorted(body["saved"]) == ["a.xml", "b.xml"]
        assert body["rejected"] == [{"filename": "notes.txt", "reason": "not_xml"}]
        assert (corpus_dir / "a.xml").read_bytes() == XML_BODY
        assert (corpus_dir / "b.xml").exists()
        assert not (corpus_dir / "notes.txt").exists()
        assert "ingest" in body["message"]

    async def test_requires_admin(self, upload_env: tuple[Any, Path, Any]) -> None:
        client, _, _ = upload_env
        token = await _login(client, "viewer")
        resp = await client.post(
            "/admin/documents",
            headers={"Authorization": f"Bearer {token}"},
            files=[("files", ("a.xml", XML_BODY, "text/xml"))],
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "forbidden"

    async def test_no_files_is_422(self, upload_env: tuple[Any, Path, Any]) -> None:
        client, _, _ = upload_env
        token = await _login(client, "admin")
        resp = await client.post(
            "/admin/documents", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 422

    async def test_traversal_name_rejected(self, upload_env: tuple[Any, Path, Any]) -> None:
        client, corpus_dir, _ = upload_env
        token = await _login(client, "admin")
        resp = await client.post(
            "/admin/documents",
            headers={"Authorization": f"Bearer {token}"},
            files=[("files", ("../evil.xml", XML_BODY, "text/xml"))],
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["saved"] == []
        assert body["rejected"][0]["reason"] == "invalid_filename"
        assert list(corpus_dir.glob("*.xml")) == [], "traversal-имя не записано на диск"

    async def test_oversize_rejected(self, upload_env: tuple[Any, Path, Any]) -> None:
        client, corpus_dir, _ = upload_env
        token = await _login(client, "admin")
        big = b"x" * (1024 * 1024 + 1)  # лимит в фикстуре - 1MB
        resp = await client.post(
            "/admin/documents",
            headers={"Authorization": f"Bearer {token}"},
            files=[("files", ("big.xml", big, "text/xml"))],
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["saved"] == []
        assert body["rejected"][0]["reason"] == "too_large"
        assert not (corpus_dir / "big.xml").exists()

    async def test_409_while_ingest_running(self, upload_env: tuple[Any, Path, Any]) -> None:
        client, _, app = upload_env
        token = await _login(client, "admin")
        app.state.ingest_status = {"state": "running"}
        resp = await client.post(
            "/admin/documents",
            headers={"Authorization": f"Bearer {token}"},
            files=[("files", ("a.xml", XML_BODY, "text/xml"))],
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "ingest_already_running"

    async def test_delete_roundtrip(self, upload_env: tuple[Any, Path, Any]) -> None:
        client, corpus_dir, _ = upload_env
        token = await _login(client, "admin")
        (corpus_dir / "tmp.xml").write_bytes(XML_BODY)
        resp = await client.delete(
            "/admin/documents/tmp.xml", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["filename"] == "tmp.xml"
        assert not (corpus_dir / "tmp.xml").exists()

        missing = await client.delete(
            "/admin/documents/tmp.xml", headers={"Authorization": f"Bearer {token}"}
        )
        assert missing.status_code == 404
        assert missing.json()["code"] == "document_not_found"

    async def test_delete_requires_admin(self, upload_env: tuple[Any, Path, Any]) -> None:
        client, corpus_dir, _ = upload_env
        (corpus_dir / "tmp.xml").write_bytes(XML_BODY)
        token = await _login(client, "viewer")
        resp = await client.delete(
            "/admin/documents/tmp.xml", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403
        assert (corpus_dir / "tmp.xml").exists()


class TestIngestFullParam:
    async def test_full_param_reaches_pipeline(
        self, itg_client: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, app = itg_client
        captured: dict[str, Any] = {}

        async def _fake_run(settings: Any, corpus_dir: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            from app.ingestion.pipeline import IngestStats

            return IngestStats()

        import app.api.admin as admin_module

        monkeypatch.setattr(admin_module, "run_ingestion", _fake_run)
        token = await _login(client, "admin")
        resp = await client.post(
            "/admin/ingest?full=true", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 202
        # фоновая задача стартует после ответа - ждём её завершения
        task = getattr(app.state, "ingest_task", None)
        if task is not None:
            await task
        assert captured.get("full") is True
