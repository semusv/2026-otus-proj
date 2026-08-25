"""Конфигурация приложения.

Все настройки — ТОЛЬКО через переменные окружения с префиксом ``APP_``
(pydantic-settings). Отсутствие обязательных переменных падает на старте
(fail-fast) с понятной ошибкой валидации.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from dotenv import dotenv_values
from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ENV_FILE = "../infra/.env"


def build_dsn(user: str, password: str, host: str, port: int, db: str) -> str:
    """Собирает DSN PostgreSQL для asyncpg, экранируя спецсимволы в кредах."""
    return (
        f"postgresql+asyncpg://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{db}"
    )


def load_env_file_into_environ(path: str = DEFAULT_ENV_FILE) -> None:
    """Доливает переменные из infra/.env в os.environ (setdefault: реальный env приоритетнее).

    Нужно для запуска CLI-скриптов (seed, миграции, ingestion) вне docker-compose.
    В контейнере файла нет - операция no-op. Пустые значения НЕ инжектируются:
    для сторонних библиотек (например, HF_ENDPOINT="") пустая строка не эквивалентна
    "не задано" и ломает дефолты (huggingface_hub собирает URL без протокола).
    """
    for key, value in dotenv_values(path).items():
        if value:  # None и "" пропускаем
            os.environ.setdefault(key, value)


class Settings(BaseSettings):
    """Типизированная модель конфигурации (префикс APP_*)."""

    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    env: Literal["dev", "stage", "prod"] = "dev"
    log_level: str = "INFO"

    # --- PostgreSQL (пользователи/роли/сессии/аудит) ---
    pg_host: str = "127.0.0.1"
    pg_port: int = Field(
        default=5432,
        validation_alias=AliasChoices("APP_PG_PORT", "APP_POSTGRES_PORT"),
    )
    pg_user: str
    pg_password: SecretStr
    pg_db: str

    # --- JWT (HS256, выпуск токенов самим backend'ом, см. ADR-007) ---
    jwt_secret: SecretStr
    jwt_ttl_minutes: int = 30

    # --- Qdrant (векторная БД, ADR-003) ---
    qdrant_url: str
    qdrant_collection: str = "chunks"

    # --- Neo4j (граф знаний, ADR-004) ---
    neo4j_uri: str
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr

    # --- LLM (OpenAI-совместимый endpoint: LM Studio dev / vLLM целевой, ADR-001) ---
    llm_base_url: str
    llm_api_key: SecretStr = SecretStr("lm-studio")
    llm_model: str

    # --- Embeddings (bge-m3 CPU-in-process, ADR-009) ---
    embedding_model: str = "BAAI/bge-m3"
    embedding_batch_size: int = Field(default=32, ge=1)
    embedding_device: Literal["cpu", "cuda"] = "cpu"

    # --- Чанкинг ---
    chunk_max_chars: int = Field(default=1800, ge=200)
    chunk_overlap_chars: int = Field(default=200, ge=0)

    # --- RAG / Query pipeline (этап 5) ---
    rag_vector_top_k: int = Field(default=8, ge=1)
    rag_final_top_n: int = Field(default=5, ge=1)
    rag_graph_hops: int = Field(default=1, ge=1, le=2)
    agent_max_iterations: int = Field(default=2, ge=1, le=5)

    # --- Генерация (vLLM/LM Studio) ---
    generate_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    generate_max_tokens: int = Field(default=1024, ge=64)

    # --- Reranker (bge-reranker-v2-m3, CPU-in-process, ADR-009) ---
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_device: Literal["cpu", "cuda"] = "cpu"

    # --- Chat / память агента (история сессии из PG) ---
    chat_history_limit: int = Field(default=8, ge=0)

    # --- Guardrails (санитайзер всегда включён; LLM-классификатор отключаемо) ---
    guardrail_max_query_chars: int = Field(default=2000, ge=100)
    guardrail_use_llm: bool = True

    # --- Трейсинг (минимальный OTel этапа 5; полные три столпа — этап 7) ---
    tracing_enabled: bool = True
    otel_exporter_endpoint: str = "http://otel-collector:4318/v1/traces"

    # --- Ingestion ---
    ingest_corpus_dir: Path = Path("../corpus_test")
    ingest_extract_concepts: bool = False
    ingest_internal_percent: int = Field(default=20, ge=0, le=100)
    ingest_secret_percent: int = Field(default=10, ge=0, le=100)
    ingest_concept_max_per_chunk: int = Field(default=6, ge=1, le=20)

    # --- Метаданные API ---
    api_title: str = "GraphRAG Platform API"
    api_version: str = "0.4.0"

    @model_validator(mode="after")
    def _validate_clearance_split(self) -> "Settings":
        """PUBLIC получает остаток; INTERNAL+SECRET обязаны оставлять место для него."""
        if self.ingest_internal_percent + self.ingest_secret_percent >= 100:
            raise ValueError(
                "Сумма APP_INGEST_INTERNAL_PERCENT и APP_INGEST_SECRET_PERCENT "
                "должна быть меньше 100 (остаток — PUBLIC)"
            )
        return self

    @property
    def async_dsn(self) -> str:
        """DSN для SQLAlchemy async-движка (asyncpg)."""
        return build_dsn(
            self.pg_user,
            self.pg_password.get_secret_value(),
            self.pg_host,
            self.pg_port,
            self.pg_db,
        )

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


def required_env(name: str, env: Mapping[str, str]) -> str:
    value = env.get(name)
    if not value:
        raise RuntimeError(f"Отсутствует обязательная переменная окружения {name}")
    return value
