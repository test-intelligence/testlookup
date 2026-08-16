"""A persistent regression must not be counted as "flaky".

Found by exploratory testing against the live homelab (2026-08-07). The seeded
project has exactly ONE genuinely alternating test, yet
``GET /api/v1/metrics/summary`` reported ``flaky_test_count: 5``.

Root cause: ``_count_flaky_tests`` decided flakiness from a failure RATIO alone
(10%-90% over the last 10 executions) with **no regard for order**. So:

    p f f f f   -> 0.8  -> counted flaky, but it is a *stable regression*
                           that has been broken for four straight builds
    p p p p f   -> 0.2  -> counted flaky, but it is a *brand-new* regression
                           that failed for the first time in the latest run

Both are the highest-value things on a QA lead's plate, and both were labelled
noise. The app's own flake engine already knows better --
``flaky_signals._label()`` calls the low-volatility/same-error case
``persistent_regression`` and says outright "do not quarantine on the flake
track". The headline KPI contradicted it.

Fix: also require ``_FLAKY_MIN_FLIPS`` (2) pass<->fail transitions in run order.
One flip is a state change (broke, or got fixed); only the second flip means the
test returned to a state it had already left, which is what intermittency is.

These tests execute the REAL production SQL against in-memory SQLite (which
supports the same ``ROW_NUMBER``/``LAG``/``FILTER`` constructs), so they pin the
query's actual semantics rather than a Python re-implementation of them.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.services.metrics_service import (  # noqa: E402
    _FLAKY_MIN_FLIPS,
    _FLAKY_MIN_RUNS,
    _count_flaky_tests,
)

PROJECT = str(uuid.uuid4())
OTHER_PROJECT = str(uuid.uuid4())

DDL = [
    """CREATE TABLE test_runs (
           id TEXT PRIMARY KEY,
           project_id TEXT,
           primary_suite_name TEXT
       )""",
    """CREATE TABLE test_cases (
           id TEXT PRIMARY KEY,
           suite_name TEXT
       )""",
    # Every run belongs to a project, and DELETE /projects/{id} is a SOFT
    # delete. The counter excludes inactive projects (2026-08-16: it reported
    # 27 flaky against a truth of 24 because deleted projects still voted), so
    # a schema without this table does not represent the query's real world.
    """CREATE TABLE projects (
           id TEXT PRIMARY KEY,
           is_active BOOLEAN
       )""",
    """CREATE TABLE test_case_history (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           test_case_id TEXT,
           test_run_id TEXT,
           test_fingerprint TEXT,
           status TEXT,
           created_at TIMESTAMP
       )""",
]

# The homelab seed, verbatim. p=PASSED f=FAILED e=BROKEN s=SKIPPED.
STATUS = {"p": "PASSED", "f": "FAILED", "e": "BROKEN", "s": "SKIPPED"}

SEEDED = {
    "test_payment_timeout":   "pfpfp",   # genuinely flaky: alternates
    "test_discount_stacking": "pffff",   # stable regression: broken since build 2
    "test_inventory_sync":    "ffppp",   # was broken, got FIXED
    "test_currency_rounding": "ppeep",   # infra blip, recovered -> intermittent
    "test_refund_flow":       "ppppf",   # brand-new regression
    "test_order_validation":  "ssppp",   # skipped, then enabled and green
    "test_cart_total":        "ppppp",   # always green
}

# What a QA lead would call flaky: it broke AND came back, more than once.
TRULY_FLAKY = {"test_payment_timeout", "test_currency_rounding"}


async def _seed(
    session,
    project_id: str,
    histories: dict[str, str],
    suite: str = "regression",
    *,
    project_active: bool = True,
):
    base = datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc)
    await session.execute(
        text(
            "INSERT OR REPLACE INTO projects (id, is_active) VALUES (:i, :a)"
        ),
        {"i": project_id, "a": project_active},
    )
    for name, pattern in histories.items():
        case_id = str(uuid.uuid4())
        await session.execute(
            text("INSERT INTO test_cases (id, suite_name) VALUES (:i, :s)"),
            {"i": case_id, "s": suite},
        )
        for idx, ch in enumerate(pattern):
            run_id = str(uuid.uuid4())
            await session.execute(
                text(
                    "INSERT INTO test_runs (id, project_id, primary_suite_name)"
                    " VALUES (:i, :p, :s)"
                ),
                {"i": run_id, "p": project_id, "s": suite},
            )
            await session.execute(
                text(
                    "INSERT INTO test_case_history"
                    " (test_case_id, test_run_id, test_fingerprint, status, created_at)"
                    " VALUES (:c, :r, :f, :st, :ts)"
                ),
                {
                    "c": case_id,
                    "r": run_id,
                    "f": f"{project_id}:{name}",
                    "st": STATUS[ch],
                    "ts": base + timedelta(hours=idx),
                },
            )
    await session.commit()


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for stmt in DDL:
            await conn.execute(text(stmt))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


class TestSeededHomelabShapes:
    @pytest.mark.asyncio
    async def test_only_genuinely_alternating_tests_are_counted(self, session):
        """The bug, end to end: 5 reported, 2 real."""
        await _seed(session, PROJECT, SEEDED)
        count = await _count_flaky_tests(session, PROJECT)
        assert count == len(TRULY_FLAKY), (
            f"expected {len(TRULY_FLAKY)} flaky tests {sorted(TRULY_FLAKY)}, got {count}. "
            "A ratio-only rule is order-blind and counts stable regressions as flaky."
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "name,pattern,reason",
        [
            ("stable_regression", "pffff", "broken for four straight runs — a bug, not a flake"),
            ("new_regression", "ppppf", "first failure in the latest run — a fresh break"),
            ("fixed_test", "ffppp", "was broken, now green — a fix, not a flake"),
        ],
    )
    async def test_single_transition_is_never_flaky(self, session, name, pattern, reason):
        """One flip is a state change, not intermittency."""
        await _seed(session, PROJECT, {name: pattern})
        assert await _count_flaky_tests(session, PROJECT) == 0, (
            f"{name} ({pattern}) was counted as flaky: {reason}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("pattern", ["pfpfp", "ppeep", "pffpp", "fppfp"])
    async def test_tests_that_recover_after_failing_are_flaky(self, session, pattern):
        """The fix must not throw away real flakes: >=2 flips still counts.

        ``ppeep`` is included deliberately — BROKEN is part of the canonical
        failed set, so an infra blip that recovers is intermittency.
        """
        await _seed(session, PROJECT, {"t": pattern})
        assert await _count_flaky_tests(session, PROJECT) == 1, (
            f"{pattern} flips at least twice and must still count as flaky"
        )


class TestPreexistingBehaviourPreserved:
    @pytest.mark.asyncio
    async def test_always_passing_and_always_failing_still_excluded(self, session):
        await _seed(session, PROJECT, {"green": "ppppp", "dead": "fffff"})
        assert await _count_flaky_tests(session, PROJECT) == 0

    @pytest.mark.asyncio
    async def test_short_history_below_min_runs_is_not_judged(self, session):
        """Fewer than _FLAKY_MIN_RUNS executions is 'no data yet', not flaky."""
        await _seed(session, PROJECT, {"t": "pf" * (_FLAKY_MIN_RUNS // 2)})
        assert await _count_flaky_tests(session, PROJECT) == 0

    @pytest.mark.asyncio
    async def test_count_is_scoped_to_the_project(self, session):
        await _seed(session, PROJECT, {"a": "pfpfp"})
        await _seed(session, OTHER_PROJECT, {"b": "pfpfp"})
        assert await _count_flaky_tests(session, PROJECT) == 1
        assert await _count_flaky_tests(session, None) == 2, "no project_id means all projects"

    @pytest.mark.asyncio
    async def test_suite_filter_still_applies(self, session):
        """The suite-filtered SQL branch is a different string — exercise it too."""
        await _seed(session, PROJECT, {"a": "pfpfp"}, suite="regression")
        await _seed(session, PROJECT, {"b": "pfpfp"}, suite="smoke")
        assert await _count_flaky_tests(session, PROJECT, "regression") == 1
        assert await _count_flaky_tests(session, PROJECT, "nope") == 0

    @pytest.mark.asyncio
    async def test_only_the_most_recent_window_is_considered(self, session):
        """A test that used to flap but has been solidly green since must age out.

        20 executions: 10 old alternating, then 10 clean passes. The window is
        the last 10, so it is not flaky any more.
        """
        await _seed(session, PROJECT, {"t": "pfpfpfpfpf" + "p" * 10})
        assert await _count_flaky_tests(session, PROJECT) == 0


class TestDeletedProjectsDoNotVote:
    """DELETE /projects/{id} flips is_active and leaves the history in place.

    Measured on the live homelab 2026-08-16: the unscoped count reported 27
    against a truth of 24, the extra three coming from projects the user had
    already deleted. Same class as the 24h failure counter beside it and as
    _period_stats before them both.
    """

    @pytest.mark.asyncio
    async def test_a_deleted_projects_flaky_tests_are_not_counted(self, session):
        await _seed(session, PROJECT, {"a": "pfpfp"})
        await _seed(session, OTHER_PROJECT, {"b": "pfpfp"}, project_active=False)
        assert await _count_flaky_tests(session, None) == 1, (
            "the unscoped count included a soft-deleted project's flaky test"
        )

    @pytest.mark.asyncio
    async def test_a_live_projects_flaky_tests_are_still_counted(self, session):
        """The filter must not become a blanket exclusion."""
        await _seed(session, PROJECT, {"a": "pfpfp"})
        await _seed(session, OTHER_PROJECT, {"b": "pfpfp"})
        assert await _count_flaky_tests(session, None) == 2

    @pytest.mark.asyncio
    async def test_the_filter_holds_on_the_suite_branch_too(self, session):
        """The suite-filtered SQL is a different string and used to emit its own
        WHERE — exercise the deleted-project rule through it as well."""
        await _seed(session, PROJECT, {"a": "pfpfp"}, suite="regression")
        await _seed(session, OTHER_PROJECT, {"b": "pfpfp"}, suite="regression",
                    project_active=False)
        assert await _count_flaky_tests(session, None, "regression") == 1


def test_the_flip_threshold_is_at_least_two():
    """Guards the fix itself: dropping it to 1 restores the original bug, since
    every single-transition regression has exactly one flip."""
    assert _FLAKY_MIN_FLIPS >= 2
