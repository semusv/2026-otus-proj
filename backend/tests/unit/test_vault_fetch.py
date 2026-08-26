"""Тесты vault_fetch: API-путь KV v2, рендер env-файла, ретраи, запись файла."""

import httpx
import pytest
from app.infra.vault_fetch import api_path, fetch, render_env, run


def test_api_path_inserts_data_segment() -> None:
    assert api_path("secret/graphrag/app") == "v1/secret/data/graphrag/app"


def test_api_path_rejects_flat_path() -> None:
    with pytest.raises(ValueError):
        api_path("secret")


def test_render_env_keeps_only_app_keys_and_escapes_quotes() -> None:
    rendered = render_env(
        {
            "APP_JWT_SECRET": "ab'c",
            "APP_LANGFUSE_PUBLIC_KEY": "pk",
            "VAULT_ROOT_TOKEN": "hvx",  # чужое пространство имён - не попадает
            "HOME": "/root",
        }
    )
    lines = rendered.splitlines()
    assert "APP_JWT_SECRET='ab'\\''c'" in lines
    assert "APP_LANGFUSE_PUBLIC_KEY='pk'" in lines
    assert len(lines) == 2


def test_run_writes_file_after_retry_on_404(tmp_path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(404, json={})
        return httpx.Response(
            200,
            json={"data": {"data": {"APP_JWT_SECRET": "s3cret", "OTHER": "x"}}},
        )

    out_file = tmp_path / "secrets.env"
    secrets = run(
        out_file,
        "http://vault:8200",
        "root",
        "secret/graphrag/app",
        retries=3,
        sleep_seconds=0.0,
        transport=httpx.MockTransport(handler),
    )
    assert secrets["APP_JWT_SECRET"] == "s3cret"
    assert len(calls) == 2
    content = out_file.read_text(encoding="utf-8")
    assert "APP_JWT_SECRET='s3cret'" in content
    assert "OTHER" not in content


def test_run_fails_when_no_app_keys(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"data": {"ONLY": "1"}}})

    with pytest.raises(RuntimeError, match="не удалось получить секрет"):
        run(
            tmp_path / "secrets.env",
            "http://vault:8200",
            "root",
            "secret/graphrag/app",
            retries=2,
            sleep_seconds=0.0,
            transport=httpx.MockTransport(handler),
        )


def test_fetch_unwraps_nested_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/secret/data/graphrag/app"
        assert request.headers["X-Vault-Token"] == "root"
        return httpx.Response(200, json={"data": {"data": {"APP_X": "y"}}})

    with httpx.Client(
        transport=httpx.MockTransport(handler), headers={"X-Vault-Token": "root"}
    ) as client:
        data = fetch(client, "http://vault:8200/", "secret/graphrag/app")
    assert data == {"APP_X": "y"}
