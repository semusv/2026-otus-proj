"""Фикстуры judge-тестов: реальный LLM из infra/.env (LM Studio / vLLM)."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from app.config import Settings

BACKEND_DIR = Path(__file__).resolve().parents[2]
INFRA_ENV = BACKEND_DIR.parent / "infra" / ".env"


@pytest.fixture(scope="session")
def judge_settings() -> Settings:
    from app.config import load_env_file_into_environ

    load_env_file_into_environ(str(INFRA_ENV))
    settings = Settings(_env_file=None)
    # хост-запуск: host.docker.internal заменяем на 127.0.0.1 (тот же порт)
    host = settings.llm_base_url.replace("host.docker.internal", "127.0.0.1")
    return settings.model_copy(update={"llm_base_url": host})
