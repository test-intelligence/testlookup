"""Real PostgreSQL proof that the per-process pool queues above its cap."""

from __future__ import annotations

import os

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="set TEST_DATABASE_URL or TESTLOOKUP_POSTGRES_TEST_DSN",
    ),
]


@pytest.mark.asyncio
async def test_server_side_app_limit_preserves_operations_and_migration() -> None:
    admin = create_async_engine(DATABASE_URL, poolclass=NullPool)
    app_role = "m12_budget_app"
    ops_role = "m12_budget_ops"
    migration_role = "m12_budget_migration"
    password = "m12-test-password"
    role_names = (app_role, ops_role, migration_role)
    base_url = make_url(DATABASE_URL)
    engines = []
    held = []
    try:
        async with admin.begin() as connection:
            for role in role_names:
                await connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
            await connection.execute(
                text(f"CREATE ROLE {app_role} LOGIN PASSWORD '{password}' CONNECTION LIMIT 6")
            )
            await connection.execute(
                text(f"CREATE ROLE {ops_role} LOGIN PASSWORD '{password}' CONNECTION LIMIT 2")
            )
            await connection.execute(
                text(
                    f"CREATE ROLE {migration_role} LOGIN PASSWORD '{password}' CONNECTION LIMIT 1"
                )
            )

        app_url = base_url.set(username=app_role, password=password)
        for index in range(2):
            engine = create_async_engine(
                app_url,
                pool_size=2,
                max_overflow=1,
                connect_args={
                    "server_settings": {"application_name": f"m12-app-{index}"}
                },
            )
            engines.append(engine)
            held.extend([await engine.connect() for _ in range(3)])

        async with admin.connect() as connection:
            count = (
                await connection.execute(
                    text("SELECT count(*) FROM pg_stat_activity WHERE usename = :role"),
                    {"role": app_role},
                )
            ).scalar_one()
            assert count == 6

        excess = create_async_engine(app_url, poolclass=NullPool)
        engines.append(excess)
        with pytest.raises(
            asyncpg.TooManyConnectionsError,
            match="too many connections|connection limit",
        ):
            await excess.connect()

        for role, application_name in (
            (ops_role, "m12-operation"),
            (migration_role, "m12-migration"),
        ):
            engine = create_async_engine(
                base_url.set(username=role, password=password),
                poolclass=NullPool,
                connect_args={"server_settings": {"application_name": application_name}},
            )
            engines.append(engine)
            async with engine.connect() as connection:
                assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1

        await held.pop().close()
        recovered = await excess.connect()
        await recovered.close()
    finally:
        for connection in held:
            await connection.close()
        for engine in engines:
            await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE usename = ANY(CAST(:roles AS text[])) AND pid <> pg_backend_pid()"
                ),
                {"roles": list(role_names)},
            )
            for role in role_names:
                await connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        await admin.dispose()


@pytest.mark.asyncio
async def test_runtime_preflight_rejects_the_undersized_ci_server(monkeypatch) -> None:
    from app.core.config import settings
    from app.db.postgres import evaluate_server_connection_budget

    admin = create_async_engine(DATABASE_URL, poolclass=NullPool)
    try:
        async with admin.connect() as connection:
            server_max = int(
                (await connection.execute(text("SHOW max_connections"))).scalar_one()
            )
            reserved = int(
                (
                    await connection.execute(text("SHOW superuser_reserved_connections"))
                ).scalar_one()
            )
        monkeypatch.setattr(settings, "PG_FLEET_MAX_CONNECTIONS", server_max + 1)
        monkeypatch.setattr(settings, "PG_FLEET_REQUIRED_CONNECTIONS", 1)
        with pytest.raises(RuntimeError, match="below declared"):
            evaluate_server_connection_budget(
                server_max=server_max,
                superuser_reserved=reserved,
                reserved=0,
                role_limit=-1,
            )
    finally:
        await admin.dispose()
