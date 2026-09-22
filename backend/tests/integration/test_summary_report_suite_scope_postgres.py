"""The summary report's suite filter: one rule for every section (VIZ-202 review).

Against real Postgres, on the reviewer's shapes:

* ``U1`` -- an upload run LABELLED ``Smoke`` whose rows are all in suite
  ``Orders`` (test A failed, B passed), 2 h ago;
* ``U0`` -- an older upload run labelled ``Orders`` with rows in ``Orders``
  (A and B passed), 1 day ago;
* ``L1`` -- a live-stream run labelled ``Payments`` whose rows never landed
  (aggregates 5 tests / 4 passed / 1 failed), 1 h ago;
* ``L2`` -- a live-stream run labelled ``Payments`` whose rows carry the
  class name ``Orders`` as ``tc.suite_name`` (test C failed), 3 h ago: its
  EFFECTIVE suite is ``Payments``;
* ``L3`` -- a rowless live-stream run labelled ``Billing`` (3 passed), 4 h ago.

Before the fix ``latest`` mode kept runs by their run LABEL (``suite_name=
Orders`` summed ``U0``'s all-green aggregates while the suites table showed
``U1``'s failure) and ``window`` mode counted runs by label-or-raw-row
(``Smoke`` counted ``U1``, which has no Smoke row; ``Orders`` counted ``L2``,
whose rows are Payments). Now every section reads rows by effective suite,
a run counts when it has such a row (or, rowless, carries the label), and
the headline is the sum of the suites table.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head. Writes one
throwaway project and removes it.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def shop():
    from app.models.postgres import Project, TestCase, TestRun

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:10]
    pid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    ids = {key: uuid.uuid4() for key in ("U1", "U0", "L1", "L2", "L3")}

    def run(key, label, source, ago, totals):
        total, passed, failed = totals
        return TestRun(
            id=ids[key], project_id=pid, build_number=f"{key}-{tag}", jenkins_job="suite-scope",
            trigger_source=source, primary_suite_name=label, total_tests=total,
            passed_tests=passed, failed_tests=failed, skipped_tests=0, broken_tests=0,
            duration_ms=1000, created_at=now - ago,
        )

    def case(key, suite, name, status, ago):
        return TestCase(
            id=uuid.uuid4(), test_run_id=ids[key], test_fingerprint=f"{tag}-{suite}-{name}"[:64],
            test_name=name, class_name=f"{suite}Test", suite_name=suite, status=status,
            created_at=now - ago,
        )

    try:
        async with sessions.begin() as db:
            db.add(Project(id=pid, name=f"suitescope-{tag}", slug=f"suitescope-{tag}",
                           is_active=True, description="throwaway summary suite-scope"))
            await db.flush()
            db.add_all([
                run("U1", "Smoke", "upload", timedelta(hours=2), (2, 1, 1)),
                run("U0", "Orders", "upload", timedelta(days=1), (2, 2, 0)),
                run("L1", "Payments", "live_stream", timedelta(hours=1), (5, 4, 1)),
                run("L2", "Payments", "live_stream", timedelta(hours=3), (1, 0, 1)),
                # The reviewer's rowless live suite, on its own.
                run("L3", "Billing", "live_stream", timedelta(hours=4), (3, 3, 0)),
            ])
            await db.flush()
            db.add_all([
                case("U1", "Orders", "test_a", "FAILED", timedelta(hours=2)),
                case("U1", "Orders", "test_b", "PASSED", timedelta(hours=2)),
                case("U0", "Orders", "test_a", "PASSED", timedelta(days=1)),
                case("U0", "Orders", "test_b", "PASSED", timedelta(days=1)),
                # A live-stream row stamped with its class name: effective suite
                # is the run label, Payments.
                case("L2", "Orders", "test_c", "FAILED", timedelta(hours=3)),
            ])
        yield sessions, pid
    finally:
        async with sessions.begin() as db:
            await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": pid})
        await engine.dispose()


async def _report(shop, mode, suites):
    from app.services.summary_report_service import build_summary_report

    sessions, pid = shop
    async with sessions() as db:
        return await build_summary_report(db, pid, 7, mode, suite_name=suites)


def _agree(report):
    """The sections describe one population."""
    suites = {s["suite_name"] for s in report["suites"]}
    totals = report["totals"]
    for key, field in (("total_test_cases", "total"), ("passed", "passed"),
                       ("failed", "failed"), ("skipped", "skipped"), ("broken", "broken")):
        assert totals[key] == sum(s[field] for s in report["suites"]), (key, report)
    assert {t["suite_name"] for t in report["top_failing_tests"]} <= suites, report
    assert (report["run_count"] == 0) == (not report["suites"]), report


def _summary(report):
    t = report["totals"]
    return {
        "runs": report["run_count"],
        "totals": (t["total_test_cases"], t["passed"], t["failed"]),
        "suites": sorted((s["suite_name"], s["total"], s["failed"]) for s in report["suites"]),
        "top": sorted((t["suite_name"], t["test_name"], t["failures"])
                      for t in report["top_failing_tests"]),
    }


CASES = {
    # (mode, suites) -> expected
    ("window", ("Orders",)): {
        "runs": 2,  # U1, U0 -- not L2 (its rows are Payments), not L1
        "totals": (2, 1, 1),
        "suites": [("Orders", 2, 1)],
        "top": [("Orders", "test_a", 1)],
    },
    ("window", ("Orders", "Payments")): {
        "runs": 4,  # U1, U0 by rows; L2 by Payments rows; L1 rowless, by label
        "totals": (3, 1, 2),
        "suites": [("Orders", 2, 1), ("Payments", 1, 1)],
        "top": [("Orders", "test_a", 1), ("Payments", "test_c", 1)],
    },
    ("window", ("Payments",)): {
        "runs": 2,  # L2 by rows, L1 by label
        "totals": (1, 0, 1),
        "suites": [("Payments", 1, 1)],
        "top": [("Payments", "test_c", 1)],
    },
    ("window", ("Orders", "Billing")): {
        "runs": 3,  # U1, U0 by rows; L3 rowless, by label
        # The headline is the table: Orders' rows plus Billing's aggregates
        # (the project-wide rule dropped Billing because Orders had rows).
        "totals": (5, 4, 1),
        "suites": [("Billing", 3, 0), ("Orders", 2, 1)],
        "top": [("Orders", "test_a", 1)],
    },
    ("window", ("Smoke",)): {  # U1 is LABELLED Smoke but has no Smoke row
        "runs": 0, "totals": (0, 0, 0), "suites": [], "top": [],
    },
    ("latest", ("Orders",)): {
        "runs": 1,  # U1: Orders' latest run by effective suite (not U0 by label)
        "totals": (2, 1, 1),
        "suites": [("Orders", 2, 1)],
        "top": [("Orders", "test_a", 1)],
    },
    ("latest", ("Orders", "Payments")): {
        "runs": 2,  # U1 for Orders; L1 (rowless, newest) for Payments
        "totals": (7, 5, 2),
        "suites": [("Orders", 2, 1), ("Payments", 5, 1)],
        "top": [("Orders", "test_a", 1), ("Payments", "test_c", 1)],
    },
    ("latest", ("Billing",)): {
        "runs": 1, "totals": (3, 3, 0), "suites": [("Billing", 3, 0)], "top": [],
    },
    ("latest", ("Smoke",)): {
        "runs": 0, "totals": (0, 0, 0), "suites": [], "top": [],
    },
}


@pytest.mark.parametrize("mode,suites", list(CASES), ids=[f"{m}-{'+'.join(s)}" for m, s in CASES])
async def test_every_section_uses_the_effective_suite(shop, mode, suites):
    report = await _report(shop, mode, suites if len(suites) > 1 else suites[0])
    assert _summary(report) == CASES[(mode, suites)], _summary(report)
    _agree(report)
    if mode == "window":
        assert report["runs_per_day"] == round(report["run_count"] / 7, 2)


@pytest.mark.parametrize("mode", ["window", "latest"])
async def test_case_and_spacing_do_not_change_the_answer(shop, mode):
    assert _summary(await _report(shop, mode, "  orders ")) == _summary(
        await _report(shop, mode, "Orders")
    )


async def test_unfiltered_report_keeps_its_rules(shop):
    """No filter: window counts every run, latest sums the latest run per
    LABEL (U1 Smoke, U0 Orders, L1 Payments, L3 Billing) as it always has."""
    window = await _report(shop, "window", None)
    assert window["run_count"] == 5
    latest = await _report(shop, "latest", None)
    assert latest["run_count"] == 4
    assert latest["totals"]["total_test_cases"] == 2 + 2 + 5 + 3
