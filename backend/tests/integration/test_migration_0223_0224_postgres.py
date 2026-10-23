"""Migrations 0223 (W10 INT-04) and 0224 (W10 INT-07) on real PostgreSQL.

0223: existing provider rows read ``scope_key = ''`` and stay unique; the key
is ``(provider, scope_key)``, so ``(github, <project id>)`` inserts beside
``(github, '')`` while a second ``(github, '')`` is refused; the history index
exists; an install whose key carries another name is swapped all the same; the
downgrade deletes the scoped rows and restores the single-column key; upgrade
-> downgrade -> upgrade again works.

0224: an existing subscription reads ``consecutive_failures = 0`` and
``breaker_threshold = 10``; ``outbound_webhooks`` flips ON only on an unused
database (no user, no project) and the downgrade mirrors it; a used database
keeps its stored value both ways.

The migrations run through a real Alembic ``Operations`` context (so every
``autocommit_block()`` and CONCURRENTLY build really runs outside a
transaction) against a SCRATCH schema: copies of the tables as 0222 left them
(``LIKE ... INCLUDING DEFAULTS`` minus the new columns), plus one-column
``users`` / ``projects`` tables for the fresh-install predicate. The scratch
schema is dropped afterwards; the shared tables are never written, and the
last test checks they still carry head's key and index.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
M0223 = "0223_integration_health_scope.py"
M0224 = "0224_webhook_circuit_breaker.py"
FLAG = "outbound_webhooks"
WEBHOOK_COLUMNS = ("consecutive_failures", "breaker_threshold", "disabled_at", "disabled_reason")


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration(filename: str):
    spec = importlib.util.spec_from_file_location(f"m_{filename[:4]}_pg", VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def conn():
    """A connection whose work commits; the scratch schema is dropped afterwards."""
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as connection:
            connection.info["scratch_schemas"] = []
            try:
                yield connection
            finally:
                await connection.rollback()
                await connection.execute(text("RESET search_path"))
                for schema in connection.info["scratch_schemas"]:
                    await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                await connection.commit()
    finally:
        await engine.dispose()


async def _scratch(conn, *, health_key: str | None = None) -> str:
    """The four tables as 0222 left them, in a fresh schema first on the search path."""
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    scratch = f"w10a_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    for table in ("integration_health_checks", "integration_probe_results", "webhook_subscriptions", "feature_flags"):
        await conn.execute(text(
            f'CREATE TABLE "{scratch}".{table} (LIKE "{base}".{table} INCLUDING DEFAULTS)'
        ))
    await conn.execute(text(f'ALTER TABLE "{scratch}".integration_health_checks DROP COLUMN scope_key'))
    key = f"CONSTRAINT {health_key} " if health_key else ""
    await conn.execute(text(f'ALTER TABLE "{scratch}".integration_health_checks ADD {key}PRIMARY KEY (provider)'))
    await conn.execute(text(f'ALTER TABLE "{scratch}".integration_probe_results DROP COLUMN scope_key'))
    await conn.execute(text(f'ALTER TABLE "{scratch}".integration_probe_results ADD PRIMARY KEY (id)'))
    for column in WEBHOOK_COLUMNS:
        await conn.execute(text(f'ALTER TABLE "{scratch}".webhook_subscriptions DROP COLUMN {column}'))
    await conn.execute(text(f'ALTER TABLE "{scratch}".webhook_subscriptions ADD PRIMARY KEY (id)'))
    # The fresh-install predicate only asks whether a row exists.
    await conn.execute(text(f'CREATE TABLE "{scratch}".users (id uuid)'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".projects (id uuid)'))
    await conn.execute(text(f'SET search_path TO "{scratch}", "{base}"'))
    await conn.commit()
    return scratch


async def _run(conn, filename: str, step: str) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    module = _migration(filename)

    def _go(sync_conn):
        context = MigrationContext.configure(sync_conn)
        module.op = Operations(context)
        with context.begin_transaction():
            getattr(module, step)()

    await conn.commit()
    await conn.run_sync(_go)
    await conn.commit()


async def _scalar(conn, sql: str, **params):
    value = (await conn.execute(text(sql), params)).scalar()
    await conn.commit()
    return value


async def _rows(conn, sql: str, **params):
    rows = (await conn.execute(text(sql), params)).all()
    await conn.commit()
    return rows


async def _primary_key(conn, schema: str) -> tuple[str, str]:
    row = (await conn.execute(text(
        "SELECT c.conname, pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace "
        "WHERE n.nspname = :schema AND t.relname = 'integration_health_checks' AND c.contype = 'p'"
    ), {"schema": schema})).one()
    await conn.commit()
    return row[0], row[1]


async def _indexes(conn, schema: str, table: str) -> set[str]:
    rows = await _rows(
        conn, "SELECT indexname FROM pg_indexes WHERE schemaname = :schema AND tablename = :table",
        schema=schema, table=table,
    )
    return {r[0] for r in rows}


async def _columns(conn, schema: str, table: str) -> set[str]:
    rows = await _rows(
        conn, "SELECT column_name FROM information_schema.columns WHERE table_schema = :schema "
              "AND table_name = :table", schema=schema, table=table,
    )
    return {r[0] for r in rows}


async def _insert_health(conn, provider: str, scope_key: str | None = None) -> None:
    if scope_key is None:
        await conn.execute(text(
            "INSERT INTO integration_health_checks (provider, status) VALUES (:p, 'healthy')"
        ), {"p": provider})
    else:
        await conn.execute(text(
            "INSERT INTO integration_health_checks (provider, scope_key, status) VALUES (:p, :s, 'healthy')"
        ), {"p": provider, "s": scope_key})
    await conn.commit()


async def _insert_probe(conn, provider: str, scope_key: str | None = None) -> None:
    columns, values = "id, provider, status", ":id, :p, 'healthy'"
    params = {"id": uuid.uuid4(), "p": provider}
    if scope_key is not None:
        columns, values = columns + ", scope_key", values + ", :s"
        params["s"] = scope_key
    await conn.execute(text(f"INSERT INTO integration_probe_results ({columns}) VALUES ({values})"), params)
    await conn.commit()


# ── 0223 ─────────────────────────────────────────────────────────────────────


async def test_0223_scopes_the_health_rows_and_round_trips(conn):
    scratch = await _scratch(conn)
    await _insert_health(conn, "github")
    await _insert_health(conn, "jira")
    await _insert_probe(conn, "github")

    await _run(conn, M0223, "upgrade")

    assert await _primary_key(conn, scratch) == (
        "integration_health_checks_pkey", "PRIMARY KEY (provider, scope_key)",
    )
    assert await _rows(conn, "SELECT provider, scope_key FROM integration_health_checks ORDER BY provider") == [
        ("github", ""), ("jira", ""),
    ]
    assert await _scalar(conn, "SELECT scope_key FROM integration_probe_results") == ""
    assert "ix_ipr_provider_scope_time" in await _indexes(conn, scratch, "integration_probe_results")
    assert "uq_integration_health_checks_provider_scope" not in await _indexes(
        conn, scratch, "integration_health_checks",
    )  # adopted: renamed to the key's name

    project = str(uuid.uuid4())
    await _insert_health(conn, "github", project)            # beside (github, '')
    await _insert_health(conn, "gitlab", project)
    await _insert_probe(conn, "github", project)
    with pytest.raises(IntegrityError):
        await _insert_health(conn, "github", "")
    await conn.rollback()
    with pytest.raises(IntegrityError):
        await _insert_health(conn, "github", project)
    await conn.rollback()

    await _run(conn, M0223, "downgrade")

    assert await _primary_key(conn, scratch) == ("integration_health_checks_pkey", "PRIMARY KEY (provider)")
    assert await _rows(conn, "SELECT provider FROM integration_health_checks ORDER BY provider") == [
        ("github",), ("jira",),
    ]
    assert await _scalar(conn, "SELECT count(*) FROM integration_probe_results") == 1
    assert "scope_key" not in await _columns(conn, scratch, "integration_health_checks")
    assert "scope_key" not in await _columns(conn, scratch, "integration_probe_results")
    assert "ix_ipr_provider_scope_time" not in await _indexes(conn, scratch, "integration_probe_results")
    assert await _indexes(conn, scratch, "integration_health_checks") == {"integration_health_checks_pkey"}
    with pytest.raises(IntegrityError):
        await _insert_health(conn, "github")
    await conn.rollback()

    await _run(conn, M0223, "upgrade")                        # upgrade again
    assert await _primary_key(conn, scratch) == (
        "integration_health_checks_pkey", "PRIMARY KEY (provider, scope_key)",
    )
    assert "ix_ipr_provider_scope_time" in await _indexes(conn, scratch, "integration_probe_results")


async def test_0223_swaps_a_key_of_another_name(conn):
    scratch = await _scratch(conn, health_key="health_provider_legacy_pk")
    await _insert_health(conn, "splunk")
    await _run(conn, M0223, "upgrade")
    assert await _primary_key(conn, scratch) == (
        "integration_health_checks_pkey", "PRIMARY KEY (provider, scope_key)",
    )
    assert await _indexes(conn, scratch, "integration_health_checks") == {"integration_health_checks_pkey"}


async def test_0223_drops_an_invalid_leftover_before_building(conn):
    """A concurrent build that failed half-way leaves an INVALID index of the
    same name; ``IF NOT EXISTS`` would skip it and the key could not adopt it."""
    scratch = await _scratch(conn)
    await conn.execute(text(
        "CREATE UNIQUE INDEX uq_integration_health_checks_provider_scope ON integration_health_checks (provider)"
    ))
    try:
        await conn.execute(text(
            "UPDATE pg_index SET indisvalid = false "
            "WHERE indexrelid = to_regclass('uq_integration_health_checks_provider_scope')"
        ))
    except Exception as exc:  # pragma: no cover - needs a superuser
        await conn.rollback()
        if "permission denied" in str(exc):
            pytest.skip("marking an index INVALID needs a superuser")
        raise
    await conn.commit()
    await _run(conn, M0223, "upgrade")
    assert await _primary_key(conn, scratch) == (
        "integration_health_checks_pkey", "PRIMARY KEY (provider, scope_key)",
    )


# ── 0224 ─────────────────────────────────────────────────────────────────────


async def _flag(conn, *, enabled: bool) -> None:
    await conn.execute(text(
        "INSERT INTO feature_flags (id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
        "VALUES (:id, :key, 'Outbound webhooks', :on, 100, now(), now())"
    ), {"id": uuid.uuid4(), "key": FLAG, "on": enabled})
    await conn.commit()


async def _flag_on(conn) -> bool:
    return bool(await _scalar(conn, "SELECT enabled_global FROM feature_flags WHERE key = :key", key=FLAG))


async def _subscription(conn) -> uuid.UUID:
    sub = uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO webhook_subscriptions (id, project_id, name, target_url) "
        "VALUES (:id, :project, 'Payments hook', 'https://hooks.example.test/x')"
    ), {"id": sub, "project": uuid.uuid4()})
    await conn.commit()
    return sub


async def test_0224_adds_the_breaker_columns_and_flips_the_flag_on_a_new_install(conn):
    scratch = await _scratch(conn)
    await _flag(conn, enabled=False)
    sub = await _subscription(conn)

    await _run(conn, M0224, "upgrade")

    row = (await conn.execute(text(
        "SELECT consecutive_failures, breaker_threshold, disabled_at, disabled_reason "
        "FROM webhook_subscriptions WHERE id = :id"
    ), {"id": sub})).one()
    await conn.commit()
    assert tuple(row) == (0, 10, None, None)
    assert await _flag_on(conn) is True
    await conn.execute(text(_migration(M0224).UPGRADE_SQL))   # the flip again is a no-op
    await conn.commit()
    assert await _flag_on(conn) is True

    await _run(conn, M0224, "downgrade")
    assert await _flag_on(conn) is False
    assert not set(WEBHOOK_COLUMNS) & await _columns(conn, scratch, "webhook_subscriptions")

    await _run(conn, M0224, "upgrade")                        # upgrade again
    assert await _flag_on(conn) is True
    assert set(WEBHOOK_COLUMNS) <= await _columns(conn, scratch, "webhook_subscriptions")


async def test_0224_leaves_a_used_database_as_its_admins_set_it(conn):
    await _scratch(conn)
    await _flag(conn, enabled=False)
    await conn.execute(text("INSERT INTO projects (id) VALUES (:id)"), {"id": uuid.uuid4()})
    await conn.commit()
    await _run(conn, M0224, "upgrade")
    assert await _flag_on(conn) is False

    await conn.execute(text("UPDATE feature_flags SET enabled_global = true WHERE key = :key"), {"key": FLAG})
    await conn.commit()
    await _run(conn, M0224, "downgrade")
    assert await _flag_on(conn) is True                       # an admin's ON survives the downgrade


async def test_0224_on_a_database_with_only_a_user_keeps_the_flag_off(conn):
    await _scratch(conn)
    await _flag(conn, enabled=False)
    await conn.execute(text("INSERT INTO users (id) VALUES (:id)"), {"id": uuid.uuid4()})
    await conn.commit()
    await _run(conn, M0224, "upgrade")
    assert await _flag_on(conn) is False


# ── The chain, and the shared tables untouched ──────────────────────────────


async def test_the_chain_upgrades_downgrades_to_0222_and_upgrades_again(conn):
    scratch = await _scratch(conn)
    await _insert_health(conn, "github")
    await _flag(conn, enabled=False)
    await _run(conn, M0223, "upgrade")
    await _run(conn, M0224, "upgrade")
    await _run(conn, M0224, "downgrade")
    await _run(conn, M0223, "downgrade")
    assert await _primary_key(conn, scratch) == ("integration_health_checks_pkey", "PRIMARY KEY (provider)")
    assert await _flag_on(conn) is False
    await _run(conn, M0223, "upgrade")
    await _run(conn, M0224, "upgrade")
    assert await _primary_key(conn, scratch) == (
        "integration_health_checks_pkey", "PRIMARY KEY (provider, scope_key)",
    )
    assert await _flag_on(conn) is True
    assert await _rows(conn, "SELECT provider, scope_key FROM integration_health_checks") == [("github", "")]


async def test_the_shared_tables_still_carry_heads_key_and_index(conn):
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    await conn.commit()
    assert await _primary_key(conn, base) == (
        "integration_health_checks_pkey", "PRIMARY KEY (provider, scope_key)",
    )
    assert "ix_ipr_provider_scope_time" in await _indexes(conn, base, "integration_probe_results")
    assert set(WEBHOOK_COLUMNS) <= await _columns(conn, base, "webhook_subscriptions")
