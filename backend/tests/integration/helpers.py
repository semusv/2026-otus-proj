"""Общие константы и хелперы интеграционных тестов."""

from app.db.seed import DEFAULT_PASSWORDS

CREDENTIALS: dict[str, str] = dict(DEFAULT_PASSWORDS)


async def seed_users(base_settings: object, pg_dsn: str) -> None:
    from app.db.seed import seed

    test_dbname = pg_dsn.rsplit("/", 1)[1]
    settings = base_settings.model_copy(update={"pg_db": test_dbname})  # type: ignore[attr-defined]
    await seed(settings)  # type: ignore[arg-type]
