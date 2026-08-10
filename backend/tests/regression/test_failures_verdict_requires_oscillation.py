"""The /failures headline must not call a stable regression an oscillating flake.

Found by exploratory testing against the live homelab (2026-08-07), driving the
real SPA. The FAILURE VERDICT card on ``/failures`` rendered:

    FAILURE VERDICT   Flaky - 5 tests intermittent
    "5 tests show pass/fail oscillation on the same SHA. Re-runs may pass
     without fixing the underlying race or fixture issue."
    "80% failure rate on test_discount_stacking - failed 4 of 5 executions."

``test_discount_stacking`` fails in four consecutive builds and passed once, at
the very start. It does **not** oscillate. The page stated that it does, as fact,
and used it as the headline example -- then advised re-running a test that will
never pass.

This was the THIRD independent flaky detector to re-derive flakiness from a
failure RATIO alone (``analytics_service.flaky_tests``, band 0.05-0.95, >=3 runs,
no order term), after ``metrics_service`` (#461) and the flaky-coach quarantine
recommendation (#462). The agents already had it right --
``anomaly_agent``: *"Flaky classification requires status transitions
(oscillation, not regression)"* -- only the read paths did not.

Fix: the auto-detector additionally requires
``flaky_signals.MIN_FLIPS_FOR_INTERMITTENCY`` pass<->fail transitions in run
order. That constant is now the single source for all three surfaces, so a
fourth copy cannot drift.

Manually-triaged FLAKY_TEST rows merged in below the auto query are deliberately
NOT gated -- a human calling a test flaky is a judgement the detector should not
override.

Executes the REAL production SQL against in-memory SQLite so it pins query
semantics rather than a Python re-implementation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.services import analytics_service  # noqa: E402
from app.services.flaky_signals import MIN_FLIPS_FOR_INTERMITTENCY  # noqa: E402

PROJECT = str(uuid.uuid4())

DDL = [
    # ``is_active`` mirrors the real column: analytics scopes every query to
    # live projects, so a stub without it errors instead of returning rows.
    "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, is_active BOOLEAN DEFAULT 1)",
    "CREATE TABLE test_runs (id TEXT PRIMARY KEY, project_id TEXT, primary_suite_name TEXT)",
    # triage_* columns are needed by the manual-triage merge that runs after the
    # auto query; without them the whole call errors rather than returning rows.
    """CREATE TABLE test_cases (
           id TEXT PRIMARY KEY, test_fingerprint TEXT, test_name TEXT,
           suite_name TEXT, class_name TEXT, test_run_id TEXT,
           triage_status TEXT, triage_updated_at TIMESTAMP
       )""",
    """CREATE TABLE test_case_history (
           id INTEGER PRIMARY KEY AUTOINCREMENT, test_case_id TEXT, test_run_id TEXT,
           test_fingerprint TEXT, status TEXT, created_at TIMESTAMP
       )""",
]

STATUS = {"p": "PASSED", "f": "FAILED", "e": "BROKEN"}

# The seeded homelab project, verbatim.
SEEDED = {
    "test_payment_timeout": "pffpp",    # 2 flips - genuinely intermittent
    "test_currency_rounding": "ppeep",  # 2 flips - infra blip that recovered
    "test_discount_stacking": "pffff",  # 1 flip  - STABLE REGRESSION
    "test_inventory_sync": "ffppp",     # 1 flip  - got fixed
    "test_refund_flow": "ppppf",        # 1 flip  - just broke
    "test_cart_total": "ppppp",         # never fails
}
OSCILLATING = {"test_payment_timeout", "test_currency_rounding"}


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
        await conn.execute(
            text(
                "INSERT INTO projects (id, name, is_active)"
                " VALUES (:i, 'Checkout Service', 1)"
            ),
            {"i": PROJECT},
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _seed(session, histories: dict[str, str], suite: str = "regression"):
    base = datetime.now(timezone.utc) - timedelta(days=1)
    for name, pattern in histories.items():
        fp = f"fp_{name}"
        case_id = str(uuid.uuid4())
        await session.execute(
            text(
                "INSERT INTO test_cases (id, test_fingerprint, test_name, suite_name, class_name)"
                " VALUES (:i, :f, :n, :s, 'Cls')"
            ),
            {"i": case_id, "f": fp, "n": name, "s": suite},
        )
        for idx, ch in enumerate(pattern):
            run_id = str(uuid.uuid4())
            await session.execute(
                text(
                    "INSERT INTO test_runs (id, project_id, primary_suite_name)"
                    " VALUES (:i, :p, :s)"
                ),
                {"i": run_id, "p": PROJECT, "s": suite},
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
                    "f": fp,
                    "st": STATUS[ch],
                    "ts": base + timedelta(minutes=idx),
                },
            )
    await session.commit()


async def _auto_names(session) -> set[str]:
    """Auto-detected rows only (``source == 'auto'``)."""
    res = await analytics_service.flaky_tests(
        session, project_id=PROJECT, days=7, limit=50
    )
    items = res["items"] if isinstance(res, dict) and "items" in res else res
    if isinstance(items, dict):
        items = list(items.values())[0]
    return {i["test_name"] for i in items if i.get("source") == "auto"}


class TestTheHomelabCase:
    @pytest.mark.asyncio
    async def test_only_oscillating_tests_are_reported(self, session):
        await _seed(session, SEEDED)
        assert await _auto_names(session) == OSCILLATING

    @pytest.mark.asyncio
    async def test_the_stable_regression_is_not_called_flaky(self, session):
        """The exact headline example that made the card false."""
        await _seed(session, {"test_discount_stacking": "pffff"})
        assert await _auto_names(session) == set(), (
            "a test broken in four consecutive builds was presented as showing "
            "'pass/fail oscillation on the same SHA', with advice to re-run it"
        )


class TestSingleTransitionsExcluded:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "pattern,why",
        [
            ("pffff", "broke and stayed broken"),
            ("ppppf", "just broke — a fresh regression"),
            ("ffppp", "got fixed — a state change, not a flake"),
            ("pppff", "broke near the end"),
        ],
    )
    async def test_one_flip_is_not_oscillation(self, session, pattern, why):
        await _seed(session, {"t": pattern})
        assert await _auto_names(session) == set(), why


class TestRealFlakesStillDetected:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("pattern", ["pfpfp", "ppeep", "pffpp", "fpfpf", "pfpff"])
    async def test_oscillating_tests_are_still_reported(self, session, pattern):
        await _seed(session, {"t": pattern})
        assert await _auto_names(session) == {"t"}, (
            f"{pattern} oscillates and must still be detected — the fix must not "
            "blunt real flake detection"
        )

    @pytest.mark.asyncio
    async def test_always_passing_and_always_failing_excluded(self, session):
        await _seed(session, {"green": "ppppp", "dead": "fffff"})
        assert await _auto_names(session) == set()

    @pytest.mark.asyncio
    async def test_below_min_runs_is_not_judged(self, session):
        await _seed(session, {"t": "pf"})
        assert await _auto_names(session) == set()


def test_all_three_surfaces_share_one_constant():
    """The drift guard.

    Three read-path detectors each re-derived flakiness from a ratio and each
    admitted stable regressions. They must now agree by construction.
    """
    from app.services.metrics_service import _FLAKY_MIN_FLIPS
    from app.services.test_health_coach_service import _MIN_FLIPS_FOR_QUARANTINE

    assert MIN_FLIPS_FOR_INTERMITTENCY >= 2
    assert _FLAKY_MIN_FLIPS is MIN_FLIPS_FOR_INTERMITTENCY
    assert _MIN_FLIPS_FOR_QUARANTINE is MIN_FLIPS_FOR_INTERMITTENCY
    assert analytics_service._FLAKY_MIN_FLIPS is MIN_FLIPS_FOR_INTERMITTENCY
