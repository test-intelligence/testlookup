"""W23a FAIL-11 against PostgreSQL at head: the four detectors and the store on the real schema.

What the unit tests cannot show without a database: the flake statement
(window functions, ``FILTER``, ``ARRAY_AGG(...)[1:5]``) over real history
rows, ``DISTINCT ON`` over ``suite_executions``, the JSONB findings, the
partial unique index ``uq_project_insights_open`` and the upsert that names
it (``xmax = 0``), ``SET NULL`` on the dismisser, and the real flag reader.

One project is seeded through the real tables: 12 runs with stored
``duration_findings`` naming suite "Payments sentinel" (newest included);
history rows giving that suite 1 flaky test last week and 4 this week over
5 + 5 runs; two coverage snapshots (100 -> 80 exercised); a quota at 85 % of a
10 USD cap with ``llm_cost_budget`` on. ``is_enabled`` is driven for real
(its cache is the Redis the wrapper exports).

Fixture rule (contract §6.4): ``dispose_engine_for_loop()`` before building
the engine and after disposing it; ``close_redis()`` in teardown (the flag
cache binds the app's Redis pool); Mongo is never bound, so ``close_mongo()``
is never called. Everything runs in one transaction that is rolled back.
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` at head; skips otherwise.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_FLAGS = ("llm_cost_budget", "cross_run_insights", "perf_regression_detection")


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def conn():
    from app.db import postgres as app_postgres
    from app.db.redis_client import close_redis
    from app.services.feature_flags import invalidate_flag_cache

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=1, max_overflow=0)
    try:
        for key in _FLAGS:
            await invalidate_flag_cache(key)
        async with engine.connect() as connection:
            outer = await connection.begin()
            try:
                yield connection
            finally:
                await outer.rollback()
    finally:
        # The flags were read inside the rolled-back transaction: drop what the
        # reader cached from it.
        for key in _FLAGS:
            await invalidate_flag_cache(key)
        await close_redis()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


def _session(connection) -> AsyncSession:
    # A commit inside releases a SAVEPOINT; the outer transaction is still rolled back.
    return AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)


async def _project(db: AsyncSession, tag: str):
    from app.models.postgres import Project

    project = Project(id=uuid.uuid4(), name=f"Insights {tag}", slug=f"insights-{tag}", is_active=True)
    db.add(project)
    await db.flush()
    return project


async def _cost_flag_on(db: AsyncSession) -> None:
    """``llm_cost_budget`` on for every project, inside the test's transaction."""
    from app.models.postgres import FeatureFlag

    row = (await db.execute(select(FeatureFlag).where(FeatureFlag.key == "llm_cost_budget"))).scalar_one_or_none()
    if row is None:
        db.add(FeatureFlag(key="llm_cost_budget", description="test", enabled_global=True, rollout_percent=100))
    else:
        row.enabled_global, row.enabled_projects, row.enabled_roles, row.rollout_percent = True, None, None, 100
    await db.flush()


def _findings(flagged: bool) -> dict:
    suites = [{
        "suite_key": "payments sentinel", "suite_name": "Payments sentinel", "metric": "wall", "ratio": 1.8,
        "recent_median_ms": 1800.0, "baseline_median_ms": 1000.0, "recent_runs": 5, "baseline_runs": 20,
    }] if flagged else []
    return {
        "schema_version": 1, "measured": True, "spikes": [], "spike_count": 0, "suites": suites,
        "suite_regression_count": len(suites), "suite_check": {"measured": True},
        "test_check": {"measured": False, "reason": "not enough history (4 of 10)"},
    }


def _flaky_status(run_index: int) -> str:
    """P F P F P over five runs: 5 results, 40 % failing, 4 status flips."""
    return "FAILED" if run_index % 2 else "PASSED"


async def _world(db: AsyncSession, now: datetime) -> dict:
    """The project of the module docstring, seeded through the real tables."""
    from app.models.postgres import (
        CoverageSnapshot,
        ProjectLlmQuota,
        ProjectLlmUsage,
        SuiteExecution,
        TestCase,
        TestCaseHistory,
        TestRun,
    )
    from app.services.llm_cost_budget import current_period_bounds

    tag = "t" + uuid.uuid4().hex[:9]
    project = await _project(db, tag)
    await _cost_flag_on(db)

    # perf_drift: 12 runs in the last 7 days whose stored findings name the suite.
    perf_runs = []
    for index in range(12):
        at = now - timedelta(hours=1 + 10 * index)
        run = TestRun(id=uuid.uuid4(), project_id=project.id, build_number=f"p{index}-{tag}", branch="main",
                      status="PASSED", created_at=at, duration_findings=_findings(True))
        db.add(run)
        await db.flush()
        db.add(SuiteExecution(project_id=project.id, test_run_id=run.id, suite_name="Payments sentinel",
                              suite_key="payments sentinel", wall_ms=1800, summed_ms=1800, source="derived",
                              run_created_at=at, branch="main"))
        perf_runs.append(run.id)

    # flake_rising: 5 runs a week; last week t1 is flaky, this week t1..t4 are.
    history: list[tuple[uuid.UUID, int, int, str]] = []
    for week, days in (("previous", range(8, 13)), ("current", range(1, 6))):
        flaky = {"t1"} if week == "previous" else {"t1", "t2", "t3", "t4"}
        for run_index, day in enumerate(days):
            at = now - timedelta(days=day, hours=2)
            run = TestRun(id=uuid.uuid4(), project_id=project.id, build_number=f"f{week}{run_index}-{tag}",
                          branch="main", status="PASSED", created_at=at)
            db.add(run)
            await db.flush()
            for name in ("t1", "t2", "t3", "t4", "t5"):
                status = _flaky_status(run_index) if name in flaky else "PASSED"
                fp = f"fp_{tag}_{name}"
                case = TestCase(test_run_id=run.id, test_name=f"checkout_sentinel_{name}", suite_name="Payments sentinel",
                                test_fingerprint=fp, status=status, created_at=at)
                db.add(case)
                await db.flush()
                row = TestCaseHistory(test_case_id=case.id, test_run_id=run.id, test_fingerprint=fp,
                                      status=status, created_at=at)
                db.add(row)
                await db.flush()
                history.append((row.id, run_index, int(name[1]), week))

    # coverage_drop: 100 -> 80 tests exercised a week apart.
    today = now.astimezone(timezone.utc).date()
    db.add(CoverageSnapshot(project_id=project.id, snapshot_day=today - timedelta(days=1), total_tests=120,
                            automated_count=80, total_suites=3))
    db.add(CoverageSnapshot(project_id=project.id, snapshot_day=today - timedelta(days=8), total_tests=120,
                            automated_count=100, total_suites=4))

    # cost_budget: 8.50 of a 10 USD cap, soft warning at 80 %.
    start, end = current_period_bounds(now)
    db.add(ProjectLlmQuota(project_id=project.id, enabled=True, included_usd=0.0, hard_cap_usd=10.0,
                           soft_warn_threshold_pct=80))
    db.add(ProjectLlmUsage(project_id=project.id, period_start=start, period_end=end, total_cost_usd=8.5))
    await db.flush()
    return {"project": project, "tag": tag, "perf_runs": perf_runs, "history": history}


async def _set_this_week(db: AsyncSession, world: dict, *, flaky: bool) -> None:
    """Make this week's t1..t4 flaky again, or all of them steady passes."""
    from app.models.postgres import TestCaseHistory

    for row_id, run_index, test_number, week in world["history"]:
        if week != "current" or test_number == 5:
            continue
        status = _flaky_status(run_index) if flaky else "PASSED"
        await db.execute(update(TestCaseHistory).where(TestCaseHistory.id == row_id).values(status=status))
    await db.flush()


async def _sweep(db: AsyncSession, project_id: uuid.UUID, now: datetime) -> dict:
    """What the beat does per project, on one session: each detector, then apply, then commit."""
    from app.services import insight_detectors, insights_service

    out = {}
    for kind, detector in insight_detectors.DETECTORS:
        result = await detector(db, project_id, now=now)
        out[kind] = (result, await insights_service.apply_result(db, project_id, result, now=now))
        await db.commit()
    return out


async def _rows(db: AsyncSession, project_id: uuid.UUID) -> list:
    from app.models.postgres import ProjectInsight

    db.expire_all()
    return list((await db.execute(
        select(ProjectInsight).where(ProjectInsight.project_id == project_id).order_by(ProjectInsight.first_seen_at)
    )).scalars().all())


def _by_kind(rows: list) -> dict:
    out: dict = {}
    for row in rows:
        out.setdefault(row.kind, []).append(row)
    return out


async def test_a_sweep_opens_four_insights_confirms_them_clears_and_reopens(conn):
    from app.models.postgres import InsightDetectorState, User
    from app.services import insights_service

    db = _session(conn)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    world = await _world(db, now)
    project_id = world["project"].id

    first = await _sweep(db, project_id, now)
    assert {kind: counts["inserted"] for kind, (_, counts) in first.items()} == {
        "perf_drift": 1, "flake_rising": 1, "coverage_drop": 1, "cost_budget": 1,
    }
    rows = _by_kind(await _rows(db, project_id))
    assert {kind: len(items) for kind, items in rows.items()} == {
        "perf_drift": 1, "flake_rising": 1, "coverage_drop": 1, "cost_budget": 1,
    }
    perf, flake = rows["perf_drift"][0], rows["flake_rising"][0]
    assert (perf.subject_key, perf.severity) == ("suite:payments sentinel", "medium")
    assert perf.evidence["flagged_runs"] == 12 and perf.evidence["newest_run_id"] == str(world["perf_runs"][0])
    assert (flake.subject_key, flake.evidence["previous"], flake.evidence["current"]) == ("suite:payments sentinel", 1, 4)
    assert flake.evidence["runs_previous"] == 5 and flake.evidence["runs_current"] == 5
    assert len(flake.evidence["examples"]) == 4
    coverage, cost = rows["coverage_drop"][0], rows["cost_budget"][0]
    assert (coverage.evidence["previous"], coverage.evidence["current"], coverage.evidence["pct"]) == (100, 80, 20)
    assert (cost.severity, cost.evidence["status"], cost.window_key) == ("medium", "SOFT_WARN", f"month:{now:%Y-%m}")
    assert insights_service.render_title("cost_budget", cost.subject_label, cost.evidence) == (
        "AI spend is at 85 % of the $10 monthly cap"
    )
    states = {s.kind: s for s in (await db.execute(
        select(InsightDetectorState).where(InsightDetectorState.project_id == project_id)
    )).scalars().all()}
    assert set(states) == {"perf_drift", "flake_rising", "coverage_drop", "cost_budget"}
    assert all(s.measured for s in states.values()) and states["flake_rising"].firing == 1

    # A second sweep confirms: still four rows, each confirmed twice.
    later = now + timedelta(minutes=5)
    await _sweep(db, project_id, later)
    rows = await _rows(db, project_id)
    assert len(rows) == 4 and {r.detection_count for r in rows} == {2}
    assert all(r.resolved_at is None for r in rows)

    # Dismiss the flake insight; the beat keeps confirming it, and it stays dismissed.
    user = User(id=uuid.uuid4(), email=f"{world['tag']}@example.com", username=f"dismisser_{world['tag']}",
                full_name="Alice Lead", hashed_password="!unusable", role="QA_ENGINEER", is_active=True)
    db.add(user)
    await db.flush()
    flake = _by_kind(rows)["flake_rising"][0]
    _, changed = await insights_service.dismiss_insight(db, project_id, flake.id, user=user, now=later)
    await db.commit()
    assert changed is True
    await _sweep(db, project_id, later + timedelta(minutes=5))
    flake = _by_kind(await _rows(db, project_id))["flake_rising"][0]
    assert flake.dismissed_at is not None and flake.detection_count == 3 and flake.resolved_at is None

    # The flake data made quiet: that row (and only it) is cleared.
    await _set_this_week(db, world, flaky=False)
    quiet_at = later + timedelta(minutes=10)
    await _sweep(db, project_id, quiet_at)
    rows = _by_kind(await _rows(db, project_id))
    assert [(r.resolved_reason, r.resolved_at) for r in rows["flake_rising"]] == [("cleared", quiet_at)]
    assert all(r.resolved_at is None for k in ("perf_drift", "coverage_drop", "cost_budget") for r in rows[k])

    # Firing again after its resolution: a NEW open row, not dismissed.
    await _set_this_week(db, world, flaky=True)
    await _sweep(db, project_id, quiet_at + timedelta(minutes=5))
    flakes = _by_kind(await _rows(db, project_id))["flake_rising"]
    assert len(flakes) == 2
    reopened = [r for r in flakes if r.resolved_at is None]
    assert len(reopened) == 1 and reopened[0].dismissed_at is None and reopened[0].detection_count == 1

    # 73 hours without a confirmation: the rest resolve stale.
    stale = await insights_service.resolve_stale(db, project_id, now=quiet_at + timedelta(hours=73))
    await db.commit()
    assert stale == 4
    rows = await _rows(db, project_id)
    assert all(r.resolved_at is not None for r in rows)
    assert sorted(r.resolved_reason for r in rows) == ["cleared", "stale", "stale", "stale", "stale"]


async def test_the_list_hides_dismissed_rows_keeps_the_dismisser_and_never_shows_another_project(conn):
    from app.models.postgres import ProjectInsight, User
    from app.services import insights_service

    db = _session(conn)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    world = await _world(db, now)
    project_id = world["project"].id
    await _sweep(db, project_id, now)
    other = await _project(db, "o" + uuid.uuid4().hex[:9])
    db.add(ProjectInsight(project_id=other.id, kind="coverage_drop", subject_key="project", subject_label="Tests exercised",
                          window_key="7d", severity="high", evidence={"previous": 50, "current": 20, "pct": 60},
                          first_seen_at=now, last_seen_at=now))
    await db.commit()

    body = await insights_service.list_insights(db, project_id, range_key="7d", include_dismissed=False,
                                                limit=50, now=now)
    assert body["enabled"] is True and body["total"] == 4 and body["dismissed_total"] == 0
    assert {item["kind"] for item in body["items"]} == {"perf_drift", "flake_rising", "coverage_drop", "cost_budget"}
    assert all(d["measured"] for d in body["detectors"])
    assert [d["kind"] for d in body["detectors"]] == ["perf_drift", "flake_rising", "coverage_drop", "cost_budget"]
    # Open rows first, then by severity: all four are open and medium here; the other project's is absent.
    assert other.id not in {item["id"] for item in body["items"]}
    theirs = await insights_service.list_insights(db, other.id, range_key="24h", include_dismissed=False,
                                                  limit=50, now=now)
    assert [item["severity"] for item in theirs["items"]] == ["high"] and theirs["total"] == 1

    user = User(id=uuid.uuid4(), email=f"{world['tag']}@example.com", username=f"dismisser_{world['tag']}",
                full_name="Alice Lead", hashed_password="!unusable", role="QA_ENGINEER", is_active=True)
    db.add(user)
    await db.flush()
    perf = next(item for item in body["items"] if item["kind"] == "perf_drift")
    # Another project's id under this project is not found.
    with pytest.raises(insights_service.InsightNotFound):
        await insights_service.dismiss_insight(db, other.id, perf["id"], user=user, now=now)
    row, changed = await insights_service.dismiss_insight(db, project_id, perf["id"], user=user, now=now)
    await db.commit()
    assert changed is True and row.dismissed_by_name == "Alice Lead"

    hidden = await insights_service.list_insights(db, project_id, range_key="7d", include_dismissed=False,
                                                  limit=50, now=now)
    assert perf["id"] not in {item["id"] for item in hidden["items"]}
    assert (hidden["total"], hidden["dismissed_total"]) == (3, 1)
    shown = await insights_service.list_insights(db, project_id, range_key="7d", include_dismissed=True,
                                                 limit=2, now=now)
    assert shown["total"] == 4 and len(shown["items"]) == 2

    # The dismisser's account goes: the name stays, the id is nulled (ON DELETE SET NULL).
    await db.delete(user)
    await db.commit()
    db.expire_all()
    kept = await db.get(ProjectInsight, perf["id"])
    assert kept.dismissed_by_id is None and kept.dismissed_by_name == "Alice Lead" and kept.dismissed_at is not None
    again = await insights_service.list_insights(db, project_id, range_key="7d", include_dismissed=True,
                                                 limit=50, now=now)
    dismissed = next(item for item in again["items"] if item["id"] == perf["id"])
    assert dismissed["dismissed_by_name"] == "Alice Lead" and dismissed["title"].startswith("Suite Payments sentinel is 1.8×")


async def test_a_second_open_row_with_the_same_identity_is_refused(conn):
    from app.models.postgres import ProjectInsight

    db = _session(conn)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    project = await _project(db, "u" + uuid.uuid4().hex[:9])

    def _row(**over):
        values = dict(project_id=project.id, kind="flake_rising", subject_key="suite:payments sentinel",
                      subject_label="Payments sentinel", window_key="7d", severity="medium", evidence={},
                      first_seen_at=now, last_seen_at=now)
        values.update(over)
        return ProjectInsight(**values)

    db.add(_row())
    await db.flush()
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(_row())
            await db.flush()
    # A RESOLVED row of the same identity is history, not a second open one.
    async with db.begin_nested():
        db.add(_row(resolved_at=now, resolved_reason="cleared"))
        await db.flush()
