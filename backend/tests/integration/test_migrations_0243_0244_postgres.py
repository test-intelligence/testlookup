"""Migrations 0243 / 0244 (W23a FAIL-11 / TRND-03) on real PostgreSQL.

* 0243 on an UPGRADED install (a user and a project already exist): the flag
  ``cross_run_insights`` is seeded with ``enabled_global = true``; where an
  administrator had already created the key by hand (``enabled_global =
  false``, another description), the row is left exactly as it was.
* Every new CHECK refuses its bad value (``kind = 'bogus'``, ``resolved_at``
  without a reason, a ``cron`` cadence with weekdays, ``grace_minutes = 721``,
  a pause with no reason, ``outcome = 'late'``, ``window_end = window_start``).
* ``uq_project_insights_open``: a second OPEN insight with the same identity is
  refused, a second RESOLVED one is admitted. The NULL-suite partial unique
  index: two "any run" cadences in one project are refused, two different
  suites are admitted. ``(cadence_id, window_start)`` is unique.
* Deleting the project removes every new row (``CASCADE``).
* Downgrade 0244 then 0243 removes the four tables and the untouched flag row
  (an edited one is kept); upgrading again rebuilds the same catalog.
* The shared database (migrated to head) carries both migrations.

The migrations run through a real Alembic ``Operations`` context against a
SCRATCH schema first on the search path, holding only the columns the two
migrations touch or reference (``projects``, ``users``, ``feature_flags``); it
is dropped afterwards and the shared tables are only read. The fixture awaits
``app.db.postgres.dispose_engine_for_loop()`` before building its engine and
after disposing it; it binds no Redis / Mongo client and closes none.
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import os
import re
import uuid
from datetime import datetime, time, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
M0243 = "0243_cross_run_insights.py"
M0244 = "0244_run_cadences.py"
NEW_TABLES = ("project_insights", "insight_detector_states", "run_cadences", "run_cadence_windows")

#: What 0243 / 0244 touch or reference, with only the columns they need.
_SCRATCH_TABLES = (
    "CREATE TABLE {s}.projects (id uuid PRIMARY KEY)",
    "CREATE TABLE {s}.users (id uuid PRIMARY KEY)",
    "CREATE TABLE {s}.feature_flags (id uuid PRIMARY KEY, key varchar(80) NOT NULL UNIQUE, description text, "
    "enabled_global boolean NOT NULL DEFAULT false, enabled_projects jsonb, enabled_roles jsonb, "
    "rollout_percent integer NOT NULL DEFAULT 100, created_at timestamptz DEFAULT now(), "
    "updated_at timestamptz DEFAULT now())",
)


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
    # Redis and Mongo are never bound here, so they are not closed here either.
    from app.db.postgres import dispose_engine_for_loop

    await dispose_engine_for_loop()
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
        await dispose_engine_for_loop()


async def _scratch(conn) -> str:
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    scratch = f"w23aa_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    for statement in _SCRATCH_TABLES:
        await conn.execute(text(statement.format(s=f'"{scratch}"')))
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


async def _rows(conn, sql: str, **params):
    rows = (await conn.execute(text(sql), params)).all()
    await conn.commit()
    return rows


async def _exec(conn, sql: str, **params) -> None:
    await conn.execute(text(sql), params)
    await conn.commit()


async def _refused(conn, sql: str, **params) -> str:
    """Run one statement that must violate a constraint; return the constraint the server names."""
    with pytest.raises(DBAPIError) as caught:
        await conn.execute(text(sql), params)
    await conn.rollback()
    message = str(caught.value.orig)
    named = re.search(r'constraint "([^"]+)"', message)
    assert named, message
    return named.group(1)


async def _tables(conn, schema: str) -> set[str]:
    rows = await _rows(conn, "SELECT table_name FROM information_schema.tables WHERE table_schema = :s", s=schema)
    return {r[0] for r in rows}


async def _catalog(conn, schema: str) -> tuple:
    """Every constraint and index of the four new tables, by name and definition."""
    constraints = await _rows(
        conn,
        "SELECT t.relname, c.conname, pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace "
        "WHERE n.nspname = :s AND t.relname = ANY(:t) ORDER BY 1, 2",
        s=schema, t=list(NEW_TABLES),
    )
    indexes = await _rows(
        conn,
        "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = :s AND tablename = ANY(:t) "
        "ORDER BY 1, 2",
        s=schema, t=list(NEW_TABLES),
    )
    return tuple(constraints), tuple((t, i, d.replace(f'"{schema}".', "").replace(f"{schema}.", "")) for t, i, d in indexes)


async def _flag(conn) -> list[tuple]:
    return await _rows(
        conn, "SELECT enabled_global, description FROM feature_flags WHERE key = 'cross_run_insights'"
    )


async def _upgraded_install(conn) -> tuple[str, uuid.UUID, uuid.UUID]:
    """A scratch schema holding a user and a project (an install being UPGRADED), then 0243 + 0244."""
    scratch = await _scratch(conn)
    user, project = uuid.uuid4(), uuid.uuid4()
    await _exec(conn, "INSERT INTO users (id) VALUES (:u)", u=user)
    await _exec(conn, "INSERT INTO projects (id) VALUES (:p)", p=project)
    await _run(conn, M0243, "upgrade")
    await _run(conn, M0244, "upgrade")
    return scratch, user, project


_INSIGHT = (
    "INSERT INTO project_insights (id, project_id, kind, subject_key, subject_label, window_key, severity, "
    "evidence, last_seen_at{cols}) VALUES (:id, :p, :kind, :subject, 'Payments sentinel', '7d', :sev, "
    "'{{}}'::jsonb, now(){vals})"
)
_CADENCE = (
    "INSERT INTO run_cadences (id, project_id, suite_name, suite_key, mode, weekdays, expected_by, cron, "
    "timezone, grace_minutes, effective_from, checked_through{cols}) VALUES (:id, :p, :suite, :key, :mode, "
    ":weekdays, CAST(:by AS time), :cron, 'Europe/Berlin', :grace, now(), now(){vals})"
)
_WINDOW = (
    "INSERT INTO run_cadence_windows (id, cadence_id, project_id, window_start, window_end, deadline, outcome) "
    "VALUES (:id, :c, :p, :start, :end, :end, :outcome)"
)


def _insight_params(project, **overrides):
    return {"id": uuid.uuid4(), "p": project, "kind": "perf_drift", "subject": "suite:payments sentinel",
            "sev": "high", **overrides}


def _cadence_params(project, **overrides):
    return {"id": uuid.uuid4(), "p": project, "suite": None, "key": None, "mode": "weekly", "weekdays": 31,
            "by": time(6, 0), "cron": None, "grace": 60, **overrides}


# ── 0243: the flag ───────────────────────────────────────────────────────────


async def test_0243_seeds_the_flag_on_for_an_upgraded_install(conn):
    await _upgraded_install(conn)
    module = _migration(M0243)
    assert await _flag(conn) == [(True, module._FLAGS[0][1])]
    rollout = await _rows(conn, "SELECT rollout_percent, enabled_projects, enabled_roles FROM feature_flags")
    assert rollout == [(100, None, None)]


async def test_0243_leaves_a_hand_made_flag_row_exactly_as_it_was(conn):
    await _scratch(conn)
    await _exec(conn, "INSERT INTO users (id) VALUES (:u)", u=uuid.uuid4())
    await _exec(
        conn,
        "INSERT INTO feature_flags (id, key, description, enabled_global, rollout_percent) "
        "VALUES (:id, 'cross_run_insights', 'Made by hand sentinel', false, 40)",
        id=uuid.uuid4(),
    )
    before = await _rows(conn, "SELECT * FROM feature_flags")
    await _run(conn, M0243, "upgrade")
    assert await _rows(conn, "SELECT * FROM feature_flags") == before
    # ... and the downgrade keeps it too (it is not the seed).
    await _run(conn, M0243, "downgrade")
    assert await _rows(conn, "SELECT * FROM feature_flags") == before


# ── the CHECKs, the unique indexes and the cascade ───────────────────────────


async def test_every_new_check_refuses_its_bad_value(conn):
    _, _, project = await _upgraded_install(conn)
    insight = _INSIGHT.format(cols="", vals="")
    resolved = _INSIGHT.format(cols=", resolved_at", vals=", now()")
    cadence = _CADENCE.format(cols="", vals="")
    paused = _CADENCE.format(cols=", paused_at, paused_until", vals=", now(), now() + interval '1 day'")

    assert "ck_project_insights_kind" == await _refused(conn, insight, **_insight_params(project, kind="bogus"))
    assert "ck_project_insights_severity" == await _refused(conn, insight, **_insight_params(project, sev="urgent"))
    assert "ck_project_insights_resolved_pair" == await _refused(conn, resolved, **_insight_params(project))
    assert "ck_run_cadences_mode_fields" == await _refused(
        conn, cadence, **_cadence_params(project, mode="cron", cron="0 6 * * 1-5", by=None)
    )
    assert "ck_run_cadences_mode" == await _refused(conn, cadence, **_cadence_params(project, mode="hourly"))
    assert "ck_run_cadences_grace" == await _refused(conn, cadence, **_cadence_params(project, grace=721))
    assert "ck_run_cadences_pause_fields" == await _refused(conn, paused, **_cadence_params(project))
    assert "ck_run_cadences_suite_pair" == await _refused(
        conn, cadence, **_cadence_params(project, suite="nightly-regression")
    )

    cadence_id = uuid.uuid4()
    await _exec(conn, cadence, **_cadence_params(project, id=cadence_id))
    start, end = datetime(2026, 10, 27, 5, tzinfo=timezone.utc), datetime(2026, 10, 28, 5, tzinfo=timezone.utc)
    window = {"c": cadence_id, "p": project, "start": start, "end": end}
    assert "ck_run_cadence_windows_outcome" == await _refused(conn, _WINDOW, id=uuid.uuid4(), outcome="late", **window)
    same = {**window, "end": window["start"]}
    assert "ck_run_cadence_windows_range" == await _refused(conn, _WINDOW, id=uuid.uuid4(), outcome="met", **same)

    # The valid shapes are admitted: a cron cadence, a weekly one with a full pause, a decided window.
    await _exec(conn, cadence, **_cadence_params(
        project, mode="cron", cron="0 6 * * 1-5", weekdays=None, by=None, suite="nightly", key="nightly",
    ))
    await _exec(conn, _CADENCE.format(cols=", paused_at, paused_until, paused_reason",
                                      vals=", now(), now() + interval '1 day', 'Release freeze sentinel'"),
                **_cadence_params(project, suite="smoke", key="smoke", grace=720))
    await _exec(conn, _WINDOW, id=uuid.uuid4(), outcome="missed", **window)
    assert await _rows(conn, "SELECT count(*) FROM run_cadences") == [(3,)]


async def test_one_open_insight_per_identity_and_any_number_resolved(conn):
    _, _, project = await _upgraded_install(conn)
    insight = _INSIGHT.format(cols="", vals="")
    resolved = _INSIGHT.format(cols=", resolved_at, resolved_reason", vals=", now(), 'cleared'")
    await _exec(conn, insight, **_insight_params(project))
    assert "uq_project_insights_open" == await _refused(conn, insight, **_insight_params(project))
    # The same subject under another kind / window, and resolved episodes, are admitted.
    await _exec(conn, insight, **_insight_params(project, kind="flake_rising"))
    await _exec(conn, resolved, **_insight_params(project))
    await _exec(conn, resolved, **_insight_params(project))
    counts = await _rows(
        conn, "SELECT kind, resolved_at IS NULL, count(*) FROM project_insights GROUP BY 1, 2 ORDER BY 1, 2"
    )
    assert counts == [("flake_rising", True, 1), ("perf_drift", False, 2), ("perf_drift", True, 1)]
    # The detector state is one row per (project, kind).
    state = "INSERT INTO insight_detector_states (project_id, kind, evaluated_at, measured) VALUES (:p, :k, now(), true)"
    await _exec(conn, state, p=project, k="coverage_drop")
    assert "insight_detector_states_pkey" == await _refused(conn, state, p=project, k="coverage_drop")
    assert "ck_insight_detector_states_kind" == await _refused(conn, state, p=project, k="bogus")


async def test_one_any_run_cadence_per_project_and_one_per_suite(conn):
    _, _, project = await _upgraded_install(conn)
    other = uuid.uuid4()
    await _exec(conn, "INSERT INTO projects (id) VALUES (:p)", p=other)
    cadence = _CADENCE.format(cols="", vals="")
    await _exec(conn, cadence, **_cadence_params(project))
    assert "uq_run_cadences_project_any" == await _refused(conn, cadence, **_cadence_params(project))
    await _exec(conn, cadence, **_cadence_params(other))                     # another project: its own "any run"
    await _exec(conn, cadence, **_cadence_params(project, suite="nightly-regression", key="nightly-regression"))
    await _exec(conn, cadence, **_cadence_params(project, suite="smoke", key="smoke"))
    assert "uq_run_cadences_project_suite" == await _refused(
        conn, cadence, **_cadence_params(project, suite="Nightly-Regression ", key="nightly-regression"),
    )
    assert await _rows(conn, "SELECT count(*) FROM run_cadences WHERE project_id = :p", p=project) == [(3,)]

    (cadence_id,) = (await _rows(conn, "SELECT id FROM run_cadences WHERE project_id = :p AND suite_key IS NULL",
                                 p=project))[0]
    start, end = datetime(2026, 10, 27, 5, tzinfo=timezone.utc), datetime(2026, 10, 28, 5, tzinfo=timezone.utc)
    window = {"c": cadence_id, "p": project, "start": start, "end": end}
    await _exec(conn, _WINDOW, id=uuid.uuid4(), outcome="met", **window)
    assert "uq_run_cadence_windows_cadence_start" == await _refused(
        conn, _WINDOW, id=uuid.uuid4(), outcome="missed", **window,
    )


async def test_deleting_the_project_removes_every_new_row_and_a_user_keeps_them(conn):
    _, user, project = await _upgraded_install(conn)
    await _exec(conn, _INSIGHT.format(cols=", dismissed_by_id, dismissed_by_name", vals=", :u, 'Alice Lead'"),
                **_insight_params(project), u=user)
    await _exec(conn, "INSERT INTO insight_detector_states (project_id, kind, evaluated_at, measured) "
                      "VALUES (:p, 'perf_drift', now(), false)", p=project)
    cadence_id = uuid.uuid4()
    await _exec(conn, _CADENCE.format(cols=", created_by_id", vals=", :u"), **_cadence_params(project, id=cadence_id),
                u=user)
    start, end = datetime(2026, 10, 27, 5, tzinfo=timezone.utc), datetime(2026, 10, 28, 5, tzinfo=timezone.utc)
    await _exec(conn, _WINDOW, id=uuid.uuid4(), outcome="met", c=cadence_id, p=project, start=start, end=end)

    await _exec(conn, "DELETE FROM users WHERE id = :u", u=user)      # SET NULL: the rows stay, the name stays
    assert await _rows(conn, "SELECT dismissed_by_id, dismissed_by_name FROM project_insights") == [(None, "Alice Lead")]
    assert await _rows(conn, "SELECT created_by_id FROM run_cadences") == [(None,)]

    await _exec(conn, "DELETE FROM projects WHERE id = :p", p=project)
    for table in NEW_TABLES:
        assert await _rows(conn, f"SELECT count(*) FROM {table}") == [(0,)], table


# ── downgrades, and upgrading again ──────────────────────────────────────────


async def test_each_downgrade_undoes_its_upgrade_and_the_upgrade_runs_again(conn):
    scratch, _, _ = await _upgraded_install(conn)
    first = await _catalog(conn, scratch)
    assert {t for t, _, _ in first[1]} == set(NEW_TABLES)       # every table has its indexes (or its PK)
    assert set(NEW_TABLES) <= await _tables(conn, scratch)

    await _run(conn, M0244, "downgrade")
    tables = await _tables(conn, scratch)
    assert "run_cadences" not in tables and "run_cadence_windows" not in tables
    assert {"project_insights", "insight_detector_states"} <= tables          # 0243 untouched
    assert len(await _flag(conn)) == 1

    await _run(conn, M0243, "downgrade")
    assert not set(NEW_TABLES) & await _tables(conn, scratch)
    assert await _flag(conn) == []                                              # the untouched seed is removed

    await _run(conn, M0243, "upgrade")
    await _run(conn, M0244, "upgrade")
    assert await _catalog(conn, scratch) == first
    assert len(await _flag(conn)) == 1


async def test_the_downgrade_keeps_a_flag_row_an_admin_edited(conn):
    await _upgraded_install(conn)
    await _exec(conn, "UPDATE feature_flags SET enabled_global = false, updated_at = now() + interval '1 second' "
                      "WHERE key = 'cross_run_insights'")
    await _run(conn, M0244, "downgrade")
    await _run(conn, M0243, "downgrade")
    assert [row[0] for row in await _flag(conn)] == [False]


# ── the shared database, at head ─────────────────────────────────────────────


async def test_the_shared_database_at_head_carries_both_migrations(conn):
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    await conn.commit()
    assert set(NEW_TABLES) <= await _tables(conn, base)
    _, indexes = await _catalog(conn, base)
    by_name = {name: definition for _, name, definition in indexes}
    assert by_name["uq_project_insights_open"].endswith(
        "USING btree (project_id, kind, subject_key, window_key) WHERE (resolved_at IS NULL)"
    )
    assert by_name["uq_run_cadences_project_any"].endswith("USING btree (project_id) WHERE (suite_key IS NULL)")
    assert by_name["uq_run_cadences_project_suite"].endswith(
        "USING btree (project_id, suite_key) WHERE (suite_key IS NOT NULL)"
    )
    flag = await _rows(conn, "SELECT count(*) FROM feature_flags WHERE key = 'cross_run_insights'")
    assert flag == [(1,)]
