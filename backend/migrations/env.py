"""Alembic async migration environment."""
import asyncio
import logging
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool, text

from app.db.migration_retry import connect_with_retry
from app.db.postgres import Base
from app.models.postgres import *  # noqa: F403 - import all models for autogenerate

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
logger = logging.getLogger("alembic.env")

# All API replicas use the same container entrypoint, so a rolling deployment
# may start several ``alembic upgrade head`` processes concurrently. A
# transaction-scoped PostgreSQL advisory lock serializes the migration body and
# is released automatically on commit, rollback, connection loss, or process
# death. The stable signed bigint is deliberately application-specific.
_ALEMBIC_ADVISORY_LOCK_ID = 6075990748104101441

# Override sqlalchemy.url from environment
db_url = (
    os.getenv("DATABASE_URL")
    or (
        f"postgresql+asyncpg://"
        f"{os.getenv('POSTGRES_USER', 'testlookup_user')}:"
        f"{os.getenv('POSTGRES_PASSWORD', '')}@"
        f"{os.getenv('POSTGRES_HOST', 'localhost')}:"
        f"{os.getenv('POSTGRES_PORT', '5433')}/"
        f"{os.getenv('POSTGRES_DB', 'testlookup')}"
    )
)
config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        if connection.dialect.name == "postgresql":
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": _ALEMBIC_ADVISORY_LOCK_ID},
            )
        context.run_migrations()


def _hide_parameters() -> bool:
    """As ``app.db.postgres.get_engine`` does: a failed migration statement's
    error text carries its bound values, and it is logged (re-audit H3)."""
    from app.core.config import settings

    return settings.APP_ENV == "production"


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        hide_parameters=_hide_parameters(),
    )

    def _log_retry(attempt: int, delay: float, exc: BaseException) -> None:
        logger.warning(
            "Database unavailable before migration (attempt %d/6); "
            "retrying in %.1fs: %s",
            attempt,
            delay,
            exc,
        )

    try:
        connection = await connect_with_retry(
            connectable.connect,
            attempts=6,
            initial_delay_seconds=1.0,
            max_delay_seconds=5.0,
            on_retry=_log_retry,
        )
        try:
            await connection.run_sync(do_run_migrations)
        finally:
            await connection.close()
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
