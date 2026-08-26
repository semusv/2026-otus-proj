"""Интеграция: контент акта по ACL (бэклог п.5) и смена грифа админом (п.4).

Контур из test_rbac_e2e: три акта PUBLIC/INTERNAL/SECRET в выделенной коллекции
Qdrant + граф; LLM не нужен - эндпоинты читают только Qdrant/Neo4j.
"""

import uuid as uuidlib
from collections.abc import AsyncIterator

import httpx
import pytest
from app.config import Settings
from app.main import create_app
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qmodels

from tests.integration.helpers import CREDENTIALS, seed_users
from tests.integration.test_rbac_e2e import (
    ACT_INT,
    ACT_PUB,
    ACT_SEC,
    INT_TEXT,
    PUB_TEXT,
    RBAC_COLLECTION,
    SEC_TEXT,
    _purge,
    _seed_corpus,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def seeded_users(base_settings: Settings, pg_dsn: str) -> None:
    await seed_users(base_settings, pg_dsn)


@pytest.fixture
def itg_settings(base_settings: Settings) -> Settings:
    return base_settings.model_copy(update={"qdrant_collection": RBAC_COLLECTION})


@pytest.fixture
async def acts_client(
    base_settings: Settings, pg_dsn: str
) -> AsyncIterator[tuple[httpx.AsyncClient, object]]:
    """Клиент приложения, чьё приложение смотрит на тестовую коллекцию Qdrant."""
    dbname = pg_dsn.rsplit("/", 1)[1]
    settings = base_settings.model_copy(
        update={
            "pg_db": dbname,
            "tracing_enabled": False,
            "qdrant_collection": RBAC_COLLECTION,
        }
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, app


@pytest.fixture
async def corpus(acts_client, itg_settings: Settings):
    """Сидированные три акта; очистка коллекции и графа после тестов."""
    qclient, neo4j_writer = await _seed_corpus(itg_settings)
    yield
    await _purge(neo4j_writer)
    await neo4j_writer.close()
    await qclient.delete_collection(RBAC_COLLECTION)
    await qclient.close()


async def _login(client: httpx.AsyncClient, username: str) -> dict:
    response = await client.post(
        "/auth/login", json={"username": username, "password": CREDENTIALS[username]}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _content(client: httpx.AsyncClient, headers: dict, act_id: str) -> httpx.Response:
    return await client.get(f"/api/acts/{act_id}/content", headers=headers)


@pytest.mark.integration
async def test_content_respects_role_matrix(corpus, acts_client) -> None:
    """Матрица доступов к полному тексту: viewer/analyst/admin x PUBLIC/INTERNAL/SECRET."""
    client, _app = acts_client
    admin = await _login(client, "admin")
    analyst = await _login(client, "analyst")
    viewer = await _login(client, "viewer")

    pub_admin = await _content(client, admin, ACT_PUB)
    assert pub_admin.status_code == 200, pub_admin.text
    body = pub_admin.json()
    assert body["act"]["act_id"] == ACT_PUB
    assert body["act"]["clearance"] == "PUBLIC"
    assert body["chunk_count"] >= 1
    assert PUB_TEXT in body["full_text"]
    assert body["act"]["doc_number"] == f"{ACT_PUB}-ФЗ"

    assert (await _content(client, viewer, ACT_PUB)).status_code == 200
    assert (await _content(client, viewer, ACT_INT)).status_code == 403
    assert (await _content(client, viewer, ACT_SEC)).status_code == 403

    int_analyst = await _content(client, analyst, ACT_INT)
    assert int_analyst.status_code == 200
    assert INT_TEXT in int_analyst.json()["full_text"]
    assert (await _content(client, analyst, ACT_SEC)).status_code == 403

    sec_admin = await _content(client, admin, ACT_SEC)
    assert sec_admin.status_code == 200
    assert SEC_TEXT in sec_admin.json()["full_text"]

    # аноним → 401; несуществующий акт → 404
    assert (await _content(client, {}, ACT_PUB)).status_code == 401
    assert (await _content(client, admin, "000000000")).status_code == 404


@pytest.mark.integration
async def test_clearance_change_flips_access_and_payload(corpus, acts_client, itg_settings) -> None:
    """PATCH грифа: доступ меняется мгновенно в обоих хранилищах и откатывается."""
    client, _app = acts_client
    admin = await _login(client, "admin")
    analyst = await _login(client, "analyst")
    viewer = await _login(client, "viewer")

    # до смены: viewer видит PUBLIC-акт, INTERNAL - нет
    assert (await _content(client, viewer, ACT_PUB)).status_code == 200
    assert (await _content(client, viewer, ACT_INT)).status_code == 403

    changed = await client.patch(
        f"/admin/acts/{ACT_PUB}/clearance", headers=admin, json={"clearance": "INTERNAL"}
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["clearance"] == "INTERNAL" and body["act_id"] == ACT_PUB

    # Neo4j-метаданные обновлены (ответ читается из графа)
    meta = await _content(client, analyst, ACT_PUB)
    assert meta.status_code == 200
    assert meta.json()["act"]["clearance"] == "INTERNAL"

    # Qdrant-payload всех точек акта перекатегоризован
    qclient = AsyncQdrantClient(url=itg_settings.qdrant_url, timeout=30)
    try:
        points, offset = [], None
        flt = qmodels.Filter(
            must=[qmodels.FieldCondition(key="act_id", match=qmodels.MatchAny(any=[ACT_PUB]))]
        )
        while True:
            batch, offset = await qclient.scroll(
                collection_name=RBAC_COLLECTION, scroll_filter=flt, limit=64,
                offset=offset, with_payload=True, with_vectors=False,
            )
            points.extend(batch)
            if offset is None:
                break
        assert points, "чанки акта должны существовать"
        assert all(p.payload.get("clearance") == "INTERNAL" for p in points)
    finally:
        await qclient.close()

    # viewer теперь не видит бывший PUBLIC-акт
    denied = await _content(client, viewer, ACT_PUB)
    assert denied.status_code == 403
    assert denied.json()["code"] == "forbidden"

    # откат грифа возвращает доступ
    restored = await client.patch(
        f"/admin/acts/{ACT_PUB}/clearance", headers=admin, json={"clearance": "PUBLIC"}
    )
    assert restored.json()["clearance"] == "PUBLIC"
    assert (await _content(client, viewer, ACT_PUB)).status_code == 200


@pytest.mark.integration
async def test_clearance_change_permissions_and_validation(corpus, acts_client) -> None:
    """Не-admin → 403; неизвестная метка → 422; неизвестный акт → 404; аноним → 401."""
    client, _app = acts_client
    admin = await _login(client, "admin")
    viewer = await _login(client, "viewer")

    assert (
        await client.patch(
            f"/admin/acts/{ACT_PUB}/clearance", headers=viewer, json={"clearance": "SECRET"}
        )
    ).status_code == 403
    assert (
        await client.patch(
            f"/admin/acts/{ACT_PUB}/clearance",
            headers={},
            json={"clearance": "SECRET"},
        )
    ).status_code == 401
    bad_value = await client.patch(
        f"/admin/acts/{ACT_PUB}/clearance", headers=admin, json={"clearance": "TOPSECRET"}
    )
    assert bad_value.status_code == 422
    not_found = await client.patch(
        f"/admin/acts/{uuidlib.uuid4().hex[:6]}", headers=admin, json={"clearance": "SECRET"}
    )
    assert not_found.status_code == 404
