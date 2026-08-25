"""Интеграция: GET /admin/stats — агрегаты наполнения Qdrant/Neo4j/PG."""

import pytest
from app.config import Settings

from tests.integration.helpers import CREDENTIALS, seed_users

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def seeded_users(base_settings: Settings, pg_dsn: str) -> None:
    await seed_users(base_settings, pg_dsn)


@pytest.mark.integration
async def test_stats_shape_and_counts(itg_client) -> None:
    client, _app = itg_client
    login = await client.post(
        "/auth/login", json={"username": "admin", "password": CREDENTIALS["admin"]}
    )
    assert login.status_code == 200

    response = await client.get(
        "/admin/stats",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert response.status_code == 200
    body = response.json()

    # структура контракта
    assert set(body) == {"qdrant", "neo4j", "postgres"}
    assert body["qdrant"]["collection"] == "chunks"
    for section in ("qdrant",):
        assert isinstance(body[section]["points"], int)
        assert body[section]["points"] >= 0

    neo4j = body["neo4j"]
    assert set(neo4j) == {"acts", "authorities", "topics", "concepts", "relationships"}
    assert all(isinstance(v, int) and v >= 0 for v in neo4j.values())

    postgres = body["postgres"]
    assert set(postgres) == {"users", "chat_sessions", "chat_messages"}
    assert all(isinstance(v, int) and v >= 0 for v in postgres.values())
    # сид-пользователи созданы миграциями/фикстурой окружения compose
    assert postgres["users"] >= 3


@pytest.mark.integration
async def test_stats_forbidden_for_viewer(itg_client) -> None:
    client, _app = itg_client
    login = await client.post(
        "/auth/login", json={"username": "viewer", "password": CREDENTIALS["viewer"]}
    )
    response = await client.get(
        "/admin/stats",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert response.status_code == 403
