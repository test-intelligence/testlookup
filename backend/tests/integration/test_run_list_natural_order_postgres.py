"""The run list's "All time" order is served by an index, even under a generic plan.

Re-audit M6. ``GET /api/v1/runs`` orders runs by a natural-sort key computed
from ``build_number`` (``ui-2`` before ``ui-10``). With ``days=0`` -- "All time"
-- nothing bounds the rows that ORDER BY sees, and no index could serve an
order whose first key is an expression, so every page sorted the project's
whole history. Migration 0167 indexes the expression, and the key now sends
its constants as SQL literals: under a generic plan a bind parameter is ``$n``,
which never matches the literal the index was built with.

The plan check uses the statement ``list_project_runs`` actually executes,
PREPAREd with ``plan_cache_mode = force_generic_plan``: the plan asyncpg's
prepared statements can settle on. A hand-typed query with literals would pass
whether or not the ORM's statement matched the index.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import asyncpg as pg_asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services import runs_service

pytestmark = pytest.mark.integration

INDEX = "ix_test_runs_project_natural_build"
LONG_BUILD = "b" + "9" * 25  # a digit run bigint cannot hold


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def engine():
    eng = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    yield eng
    await eng.dispose()


async def _new_project(conn) -> uuid.UUID:
    pid = uuid.uuid4()
    await conn.execute(
        text("INSERT INTO projects (id, name, slug, is_active) VALUES (:id, 'm6', :slug, true)"),
        {"id": pid, "slug": f"m6-{pid.hex}"},
    )
    return pid


_RUN_COLUMNS = (
    "(id, project_id, build_number, jenkins_job, status, ingestion_source, "
    "failed_tests, total_tests)"
)


@pytest.fixture
async def big_project(engine):
    """3,000 runs in one project, analysed so the planner sees them."""
    async with engine.begin() as conn:
        pid = await _new_project(conn)
        await conn.execute(text(
            f"INSERT INTO test_runs {_RUN_COLUMNS} "
            "SELECT gen_random_uuid(), :pid, 'ui-' || g, 'm6', 'PASSED', 'unknown', 0, 1 "
            "FROM generate_series(1, 3000) AS g"
        ), {"pid": pid})
        await conn.execute(text("ANALYZE test_runs"))
    yield pid
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM projects WHERE id = :id"), {"id": pid})


@pytest.fixture
async def odd_builds(engine):
    """Build numbers that exercise the natural order, including one bigint cannot hold."""
    builds = ["ui-2", "ui-10", "release", "9.9.9", "10.0.1", LONG_BUILD]
    async with engine.begin() as conn:
        pid = await _new_project(conn)
        for build in builds:
            # The INSERT itself is a check: the index expression evaluates here.
            await conn.execute(text(
                f"INSERT INTO test_runs {_RUN_COLUMNS} "
                "VALUES (gen_random_uuid(), :pid, :build, 'm6', 'PASSED', 'unknown', 0, 1)"
            ), {"pid": pid, "build": build})
    yield pid
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM projects WHERE id = :id"), {"id": pid})


async def _page_statement(engine, project_id: uuid.UUID):
    """Run the real listing (days=0, first page) and return the LIMITed SELECT it issued."""
    captured = []
    async with AsyncSession(engine) as db:
        real_execute = db.execute

        async def _spy(statement, *args, **kwargs):
            captured.append(statement)
            return await real_execute(statement, *args, **kwargs)

        db.execute = _spy
        await runs_service.list_project_runs(
            db, project_id=str(project_id), page=1, size=20, days=0
        )
    for statement in captured:
        sql = str(statement.compile(dialect=pg_asyncpg.dialect()))
        if "ORDER BY" in sql and "LIMIT" in sql and "test_runs" in sql:
            return statement
    raise AssertionError("list_project_runs issued no LIMITed, ordered SELECT")


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return f"'{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


async def _generic_plan(engine, statement) -> dict:
    compiled = statement.compile(
        dialect=pg_asyncpg.dialect(), compile_kwargs={"render_postcompile": True}
    )
    params = compiled.construct_params()
    args = ", ".join(_literal(params[name]) for name in compiled.positiontup)
    async with engine.connect() as conn:
        driver = (await conn.get_raw_connection()).driver_connection
        await driver.execute("SET plan_cache_mode = force_generic_plan")
        await driver.execute(f"PREPARE m6_page AS {compiled}")
        try:
            raw = await driver.fetchval(f"EXPLAIN (FORMAT JSON) EXECUTE m6_page({args})")
        finally:
            await driver.execute("DEALLOCATE m6_page")
            await driver.execute("RESET plan_cache_mode")
    plan = json.loads(raw) if isinstance(raw, str) else raw
    return plan[0]["Plan"]


def _nodes(plan: dict):
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


async def test_the_index_exists_and_is_valid(engine):
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT pg_get_indexdef(i.indexrelid), i.indisvalid FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
        ), {"name": INDEX})).first()
    assert row is not None, f"{INDEX} is missing: migration 0167 did not run"
    definition, valid = row
    assert valid, f"{INDEX} is INVALID: a failed CONCURRENTLY build was kept"
    assert "(project_id, (" in definition, definition
    assert "numeric[]" in definition, definition


async def test_all_time_is_an_index_scan_even_under_a_generic_plan(engine, big_project):
    plan = await _generic_plan(engine, await _page_statement(engine, big_project))
    nodes = list(_nodes(plan))
    rendered = json.dumps(plan)[:3000]
    assert any(node.get("Index Name") == INDEX for node in nodes), (
        f"the run list does not use {INDEX} under a generic plan: {rendered}"
    )
    assert not any(node["Node Type"] == "Sort" for node in nodes), (
        f"the run list still sorts: {rendered}"
    )


async def test_the_order_is_natural_and_a_long_digit_run_is_listed(engine, odd_builds):
    statement = await _page_statement(engine, odd_builds)  # raises if the listing errors
    async with AsyncSession(engine) as db:
        rows = (await db.execute(statement)).all()
    order = [row[0].build_number for row in rows]
    assert order == ["release", LONG_BUILD, "10.0.1", "ui-10", "9.9.9", "ui-2"], order
