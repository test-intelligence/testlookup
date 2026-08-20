"""A suite's test list must not contain other suites' tests.

``test_suite_service.list_legacy_suite_test_cases`` synthesizes per-test-case
rows for a suite when ``canonical_test_cases`` has none. It joined
``TestCase -> TestRun`` and ORed the per-row ``suite_name`` with the run-level
``primary_suite_name``, with **no** ``trigger_source`` restriction.

The run-level arm is right for live-stream/SDK runs: the SDK stamps the suite
once at session-create and per-event ``TestCase.suite_name`` stays NULL, so
there every case in the run really does belong to ``primary_suite_name``. It is
wrong for an upload — a multi-``<testsuite>`` upload has an authoritative
per-row suite AND a run-level label naming just one of them.

Measured on the live homelab, project 2aefa4fa scoped to ``api``::

    truth: per-row suite = api            5 distinct tests
    clause as written                    12     <- the whole run
    clause restricted to live_stream      5

The extra 7 are ``regression`` and ``smoke`` tests listed as members of the
``api`` suite — not an inflated count but wrong membership, which is worse:
the page names specific tests that do not belong to the suite you opened.

Second instance found by working the backlog's "hand-rolled copy of a shared
helper" method: list a shared helper's call sites, find code doing the
equivalent inline, and build a repro rather than argue from source. Siblings:
``metrics_service._suite_match_clause``, ``run_compare_service`` (#559),
``test_management_service`` (#560).

As with the metrics fix, these assertions execute the real query's WHERE clause
against rows rather than inspecting source.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.dialects import sqlite as sqlite_dialect  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.services.test_suite_service import (  # noqa: E402
    legacy_suite_membership_clause,
)

pytestmark = pytest.mark.regression

DDL = [
    """CREATE TABLE test_runs (
           id TEXT PRIMARY KEY, project_id TEXT, trigger_source TEXT,
           primary_suite_name TEXT
       )""",
    """CREATE TABLE test_cases (
           id TEXT PRIMARY KEY, test_run_id TEXT, test_fingerprint TEXT,
           test_name TEXT, suite_name TEXT
       )""",
]

# The upload shape: one run, three real suites, run-level label "api".
UPLOAD_SUITES = ["api"] * 5 + ["regression"] * 4 + ["smoke"] * 3


def _compiled(suite_key: str) -> str:
    """Compile the SERVICE's own predicate — imported, never re-declared.

    An earlier draft of this file re-declared an identical copy of the clause
    and claimed that made service drift visible. It does not: a copy keeps
    passing after the original changes, so the test would have guarded
    itself rather than the code.
    """
    return str(
        legacy_suite_membership_clause(suite_key).compile(
            dialect=sqlite_dialect.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


async def _engine_with(rows):
    """rows: list of (run_id, trigger_source, primary_suite_name, [suite,...])"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for stmt in DDL:
            await conn.execute(text(stmt))
        for run_id, trigger, primary, suites in rows:
            await conn.execute(
                text(
                    "INSERT INTO test_runs (id, project_id, trigger_source, primary_suite_name) "
                    "VALUES (:i, 'p1', :t, :p)"
                ),
                {"i": run_id, "t": trigger, "p": primary},
            )
            for n, suite in enumerate(suites):
                await conn.execute(
                    text(
                        "INSERT INTO test_cases "
                        "(id, test_run_id, test_fingerprint, test_name, suite_name) "
                        "VALUES (:i, :r, :f, :n, :s)"
                    ),
                    {"i": f"{run_id}-c{n}", "r": run_id, "f": f"fp-{run_id}-{n}",
                     "n": f"test_{n}", "s": suite},
                )
    return engine


async def _distinct_tests(engine, suite_key: str) -> int:
    sql = (
        "SELECT count(DISTINCT test_cases.test_fingerprint) FROM test_cases "
        "JOIN test_runs ON test_runs.id = test_cases.test_run_id "
        f"WHERE {_compiled(suite_key)}"
    )
    async with engine.connect() as conn:
        return (await conn.execute(text(sql))).scalar_one()


@pytest.mark.asyncio
async def test_an_upload_suite_lists_only_its_own_tests():
    """The measured case: 12 tests listed where 5 belong to the suite."""
    engine = await _engine_with([("run-101", "api", "api", UPLOAD_SUITES)])
    try:
        assert await _distinct_tests(engine, "api") == 5, (
            "the suite listing includes tests from other suites — a "
            "multi-suite upload whose primary_suite_name is 'api' matches the "
            "whole run (measured live: 12 distinct tests vs a truth of 5)"
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("suite,expected", [("api", 5), ("regression", 4), ("smoke", 3)])
async def test_membership_is_consistent_across_suites(suite, expected):
    """Only the suite equal to ``primary_suite_name`` was wrong, so the page
    was right for two suites and wrong for the third — the asymmetry that
    makes this survive a casual look."""
    engine = await _engine_with([("run-101", "api", "api", UPLOAD_SUITES)])
    try:
        assert await _distinct_tests(engine, suite) == expected
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_live_stream_run_still_resolves_through_the_run_label():
    """The reason the run-level arm exists — per-row suite is NULL there."""
    engine = await _engine_with([("run-live", "live_stream", "e2e", [None] * 4)])
    try:
        assert await _distinct_tests(engine, "e2e") == 4, (
            "a live_stream run with NULL per-row suites no longer resolves "
            "through its run-level label — the fallback was narrowed too far"
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_live_stream_run_does_not_absorb_a_different_suite():
    engine = await _engine_with([("run-live", "live_stream", "e2e", [None] * 4)])
    try:
        assert await _distinct_tests(engine, "smoke") == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_both_run_kinds_together():
    """Mixed project: the upload contributes only its own suite's tests while
    the live_stream run still contributes all of its own."""
    engine = await _engine_with([
        ("run-101", "api", "api", UPLOAD_SUITES),
        ("run-live", "live_stream", "api", [None] * 3),
    ])
    try:
        # 5 from the upload's real 'api' rows + 3 from the live_stream run.
        assert await _distinct_tests(engine, "api") == 8
    finally:
        await engine.dispose()


def test_the_run_level_arm_is_gated_on_trigger_source():
    """Pin the mechanism, not only the counts."""
    sql = _compiled("api").lower()
    assert "primary_suite_name" in sql, "the run-level fallback was removed entirely"
    assert "live_stream" in sql, (
        "the run-level arm is not restricted to live_stream runs — the shape "
        "that listed a whole run under one of its suites"
    )
