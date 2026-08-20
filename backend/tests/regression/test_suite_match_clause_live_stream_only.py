"""A suite filter must not return the whole run.

``metrics_service._suite_match_clause`` ORed the per-row suite with the
run-level ``test_runs.primary_suite_name`` and applied **no**
``trigger_source`` restriction. The run-level arm exists for live-stream/SDK
runs, where the SDK stamps the suite once at session-create and per-event
``TestCase.suite_name`` stays NULL — there, "every case in this run belongs to
``primary_suite_name``" is true.

It is not true for a file upload. A multi-``<testsuite>`` upload has an
authoritative per-row ``suite_name`` AND a run-level ``primary_suite_name``
naming just *one* of those suites, so filtering to that suite matched every row
in the run via the second arm.

Measured on the live homelab, project 2aefa4fa (Checkout Service), runs
101+102 — ``trigger_source=api``, ``primary_suite_name='api'``, 12 cases each
across suites api/regression/smoke::

    truth: per-row suite = api          10
    clause as written                   24     <- both runs, entire
    clause restricted to live_stream    10

That fed ``active_defects`` and ``new_failures_24h``; the latter drives the
``max_new_failures_24h`` release-gate cap, so an over-count can flip a verdict.

Nothing the fallback exists to serve is lost: on that deployment every
NULL/blank per-row ``suite_name`` belongs to a ``live_stream`` run (13 of them;
``api`` and ``push`` have zero), which is why the restriction is safe.

Third instance of this shape — ``run_compare_service`` (#559) and
``test_management_service`` (#560) were fixed the same way.

The assertions execute the REAL production clause: it is compiled to SQL and
run against rows in sqlite, so this measures row counts rather than inspecting
the source of a predicate.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.dialects import sqlite as sqlite_dialect  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.services.metrics_service import _suite_match_clause  # noqa: E402

pytestmark = pytest.mark.regression

DDL = [
    """CREATE TABLE test_runs (
           id TEXT PRIMARY KEY, project_id TEXT, build_number TEXT,
           trigger_source TEXT, primary_suite_name TEXT
       )""",
    """CREATE TABLE test_cases (
           id TEXT PRIMARY KEY, test_run_id TEXT, test_name TEXT, suite_name TEXT
       )""",
]

# Runs 101 and 102 as they exist on the deployment: an upload, run-level label
# "api", per-row suites spread across three real suites.
UPLOAD_RUNS = [
    ("run-101", "api", "api"),
    ("run-102", "api", "api"),
]
UPLOAD_SUITES = ["api"] * 5 + ["regression"] * 4 + ["smoke"] * 3   # 12 per run


def _compiled(suite: str) -> str:
    """The production clause, as SQL a database can execute."""
    return str(
        _suite_match_clause(suite).compile(
            dialect=sqlite_dialect.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


async def _engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for stmt in DDL:
            await conn.execute(text(stmt))
    return engine


async def _seed_upload_runs(engine) -> None:
    async with engine.begin() as conn:
        for run_id, trigger, primary in UPLOAD_RUNS:
            await conn.execute(
                text(
                    "INSERT INTO test_runs "
                    "(id, project_id, build_number, trigger_source, primary_suite_name) "
                    "VALUES (:i, 'p1', :b, :t, :p)"
                ),
                {"i": run_id, "b": run_id[-3:], "t": trigger, "p": primary},
            )
            for n, suite in enumerate(UPLOAD_SUITES):
                await conn.execute(
                    text(
                        "INSERT INTO test_cases (id, test_run_id, test_name, suite_name) "
                        "VALUES (:i, :r, :n, :s)"
                    ),
                    {"i": f"{run_id}-c{n}", "r": run_id, "n": f"test_{n}", "s": suite},
                )


async def _count(engine, suite: str) -> int:
    sql = (
        "SELECT count(*) FROM test_cases "
        "JOIN test_runs ON test_runs.id = test_cases.test_run_id "
        f"WHERE {_compiled(suite)}"
    )
    async with engine.connect() as conn:
        return (await conn.execute(text(sql))).scalar_one()


# ── The bug ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_upload_suite_filter_returns_only_that_suite():
    """The measured case: 24 rows came back where the truth is 10."""
    engine = await _engine()
    await _seed_upload_runs(engine)
    try:
        assert await _count(engine, "api") == 10, (
            "the suite filter returned rows outside the requested suite — a "
            "multi-suite upload whose primary_suite_name is 'api' matches the "
            "whole run through the run-level arm (measured live: 24 vs 10)"
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("suite,expected", [("api", 10), ("regression", 8), ("smoke", 6)])
async def test_every_suite_is_counted_consistently(suite, expected):
    """The over-match was not uniform, which is what made it hard to see: only
    the suite that happened to equal ``primary_suite_name`` was inflated, so
    the same page behaved differently depending on which suite you picked."""
    engine = await _engine()
    await _seed_upload_runs(engine)
    try:
        assert await _count(engine, suite) == expected
    finally:
        await engine.dispose()


# ── The case the fallback exists for — must keep working ─────────────────────


@pytest.mark.asyncio
async def test_a_live_stream_run_still_matches_on_the_run_level_label():
    """The reason the run-level arm exists. The SDK sends the suite once at
    session-create; per-event suite_name stays NULL. Narrowing the arm must
    not break this, or the fix trades an over-count for an under-count."""
    engine = await _engine()
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO test_runs (id, project_id, build_number, trigger_source, "
            "primary_suite_name) VALUES ('run-live', 'p1', '200', 'live_stream', 'e2e')"
        ))
        for n in range(4):
            await conn.execute(
                text(
                    "INSERT INTO test_cases (id, test_run_id, test_name, suite_name) "
                    "VALUES (:i, 'run-live', :n, NULL)"
                ),
                {"i": f"live-c{n}", "n": f"test_{n}"},
            )
    try:
        assert await _count(engine, "e2e") == 4, (
            "a live_stream run with NULL per-row suites no longer matches its "
            "own run-level suite — the fallback has been narrowed too far"
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_live_stream_run_does_not_match_a_different_suite():
    """Narrowing the arm must not turn it into a wildcard either."""
    engine = await _engine()
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO test_runs (id, project_id, build_number, trigger_source, "
            "primary_suite_name) VALUES ('run-live', 'p1', '200', 'live_stream', 'e2e')"
        ))
        await conn.execute(text(
            "INSERT INTO test_cases (id, test_run_id, test_name, suite_name) "
            "VALUES ('live-c0', 'run-live', 'test_0', NULL)"
        ))
    try:
        assert await _count(engine, "smoke") == 0
    finally:
        await engine.dispose()


# ── The vocabulary itself ────────────────────────────────────────────────────


def test_the_run_level_arm_is_gated_on_trigger_source():
    """Pin the mechanism, not just the counts: the run-level comparison must
    be conjoined with the live_stream restriction. A future edit that drops
    the gate would restore the over-match on data this fixture does not
    happen to contain."""
    sql = _compiled("api").lower()
    assert "primary_suite_name" in sql, "the run-level fallback has been removed entirely"
    assert "live_stream" in sql, (
        "the run-level arm is not restricted to live_stream runs — this is the "
        "exact shape that returned whole runs for multi-suite uploads"
    )
