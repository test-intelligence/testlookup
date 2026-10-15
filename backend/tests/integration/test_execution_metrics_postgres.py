"""EXEC-03 on real PostgreSQL, through the real app: the execution-metrics reads.

What only the database and the assembled app can prove:

1. **Run timing.** A 2-worker run's wall clock (9.0 s), summed time (17.4 s) and parallelism (1.93x) as EXEC-02
   wrote them, two worker lanes, the timeline in start order with offsets, the slowest test with its baseline, the
   phase totals; an old run without timestamps answers ``derived_sum`` and a timeline that says "report carried no
   timestamps".
2. **The index.** The slowest-N read uses ``ix_test_cases_run_duration`` (EXPLAIN).
3. **Suite history / execution metrics.** Oldest-first points, window percentiles over every execution, the branch
   filter, the per-day zero-fill, the suite regression rule (5 slow runs listed; 4 runs of history not), a test
   regression from its baseline, the slowest tests by p95, the duration histogram -- and nothing from another
   project, whose rows share the same suite key and the same fingerprint.
4. **Authorisation.** A run of another project is 404; a project the caller cannot read is the empty/denied
   shape with none of its data; a ``release_id`` of another project is refused before any data is read.
5. **Chart data.** ``wall_clock_p95`` by day on the run grain; by suite a 422 ``run_only_metric``.
6. **Test history.** Points carry the row's timing, the response the window percentiles and the baseline.

Every row is written by this module under a unique tag and removed at the end (the database is shared). Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head; uses ``REDIS_URL`` when set, else ``fakeredis``. Listed in the
``postgres-integration`` job's file list in ``.github/workflows/ci.yml``.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

NOW = datetime.now(timezone.utc).replace(microsecond=0)
#: The parallel run's tests started here (an hour ago, so inside every window).
T0 = NOW - timedelta(hours=1)


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


def _redis_client():
    url = os.environ.get("REDIS_URL", "").strip()
    if url:
        import redis.asyncio as redis_asyncio

        return redis_asyncio.Redis.from_url(url, decode_responses=True)
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def _run(project_id, build, *, created_at, branch="main", **over):
    from app.models.postgres import LaunchStatus, TestRun

    row = TestRun(
        id=uuid.uuid4(), project_id=project_id, build_number=build, jenkins_job="exec03",
        status=LaunchStatus.PASSED, ingestion_source="upload", branch=branch, **over,
    )
    row.created_at = created_at
    return row


def _case(run_id, name, suite, duration, *, fp=None, start=None, finish=None, worker=None, status="PASSED",
          **over):
    from app.models.postgres import TestCase

    return TestCase(
        id=uuid.uuid4(), test_run_id=run_id, test_name=name, class_name=suite, suite_name=suite,
        test_fingerprint=fp or uuid.uuid4().hex[:32], status=status, duration_ms=duration,
        started_at=start, finished_at=finish, worker_id=worker,
        timing_source="report" if start is not None else None, **over,
    )


def _suite_row(project_id, run, suite, wall, summed, *, branch="main"):
    from app.models.postgres import SuiteExecution

    return SuiteExecution(
        id=uuid.uuid4(), project_id=project_id, test_run_id=run.id, suite_name=suite, suite_key=suite.lower(),
        wall_ms=wall, summed_ms=summed, test_count=1, timed_count=1 if wall else 0, passed=1,
        parallelism=round(summed / wall, 2) if wall else None,
        source="report" if wall else "derived_sum", run_created_at=run.created_at, branch=branch,
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import (
        PerfBaseline,
        Project,
        ProjectMember,
        Release,
        TestCaseHistory,
        User,
        UserRole,
    )
    from app.services.suite_execution_service import upsert_for_run

    dsn = _env("TESTLOOKUP_POSTGRES_TEST_DSN")
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = _redis_client()

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    patch = pytest.MonkeyPatch()
    app.dependency_overrides[get_db] = _get_db
    patch.setattr("app.db.redis_client.get_redis", lambda: redis)
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    tag = uuid.uuid4().hex[:10]
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    admin, member, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    r2 = uuid.uuid4()
    w = SimpleNamespace(tag=tag, p1=p1, p2=p2, r2=r2)
    try:
        async with sessions() as db:
            db.add(Project(id=p1, name=f"exec03-p1-{tag}", slug=f"exec03-p1-{tag}", is_active=True))
            db.add(Project(id=p2, name=f"exec03-p2-{tag}", slug=f"exec03-p2-{tag}", is_active=True))
            for uid, label, role in (
                (admin, "admin", UserRole.ADMIN),
                (member, "member", UserRole.QA_ENGINEER),
                (outsider, "outsider", UserRole.QA_ENGINEER),
            ):
                db.add(User(id=uid, email=f"exec03-{label}-{tag}@example.com", username=f"exec03_{label}_{tag}",
                            full_name=f"Exec03 {label}", hashed_password="!unusable", role=role.value))
            await db.flush()
            db.add(ProjectMember(project_id=p1, user_id=member, role=UserRole.QA_ENGINEER.value))
            db.add(ProjectMember(project_id=p2, user_id=outsider, role=UserRole.QA_ENGINEER.value))
            db.add(Release(id=r2, project_id=p2, name=f"exec03-r2-{tag}", version="2.0.0", status="active"))
            await db.flush()

            # 1. The parallel run: two workers, two suites, 9.0 s on the clock, 17.4 s of test time.
            par = _run(p1, f"par-{tag}", created_at=T0)
            db.add(par)
            await db.flush()
            s = lambda secs: T0 + timedelta(seconds=secs)  # noqa: E731
            w.t_setup = _case(par.id, "test_login", "checkout", 4500, fp=f"fplogin{tag}", start=s(0), finish=s(4.5),
                              worker="0", setup_ms=120, call_ms=4300, teardown_ms=80)
            w.t_slow = _case(par.id, "test_refund", "payments", 5400, fp=f"fprefund{tag}", start=s(3.0),
                             finish=s(8.4), worker="1", status="FAILED")
            par_cases = [
                w.t_setup,
                _case(par.id, "test_cart", "checkout", 4500, start=s(4.5), finish=s(9.0), worker="0"),
                _case(par.id, "test_pay", "payments", 3000, start=s(0), finish=s(3.0), worker="1"),
                w.t_slow,
            ]
            db.add_all(par_cases)
            # The slow test has a baseline in THIS project (and a bigger one in p2 that must not be read).
            db.add(PerfBaseline(project_id=p1, test_fingerprint=w.t_slow.test_fingerprint, test_name="test_refund",
                                suite_name="payments", sample_count=14, mean_ms=4000.0, m2=0.0, stddev_ms=50.0,
                                p95_ms=4200.0, last_observed_ms=4000, last_observed_at=T0 - timedelta(hours=2)))
            db.add(PerfBaseline(project_id=p2, test_fingerprint=w.t_slow.test_fingerprint, test_name="leak",
                                suite_name="leak", sample_count=99, mean_ms=1.0, m2=0.0, stddev_ms=0.0, p95_ms=1.0))
            # History rows for the test-history additions.
            for case in (w.t_setup, w.t_slow):
                db.add(TestCaseHistory(test_case_id=case.id, test_run_id=par.id, test_fingerprint=case.test_fingerprint,
                                       status=case.status, duration_ms=case.duration_ms, created_at=T0))

            # 2. An old run: durations only (a JUnit upload before EXEC-01).
            old = _run(p1, f"old-{tag}", created_at=NOW - timedelta(hours=2))
            db.add(old)
            await db.flush()
            db.add_all([_case(old.id, f"test_old_{i}", "legacy", 1000 * (i + 1)) for i in range(3)])

            # 3. Suite histories (EXEC-02 rows written directly): "slowsuite" on main got 2x slower over its last 5
            #    runs; on dev it did not; "fewsuite" has only 4 runs, all slow.
            w.history_runs = []
            for i in range(25):
                created = NOW - timedelta(hours=3 + i)
                run = _run(p1, f"hist-{i}-{tag}", created_at=created, wall_clock_ms=2000 if i < 5 else 1000,
                           duration_ms=3000, timing_source="report")
                dev = _run(p1, f"dev-{i}-{tag}", created_at=created, branch="dev", wall_clock_ms=1000,
                           duration_ms=3000, timing_source="report")
                db.add_all([run, dev])
                await db.flush()
                db.add(_suite_row(p1, run, "SlowSuite", 2000 if i < 5 else 1000, 3000))
                db.add(_suite_row(p1, dev, "SlowSuite", 1000, 3000, branch="dev"))
                if i < 4:
                    db.add(_suite_row(p1, run, "FewSuite", 2000, 2000))
                # One baselined test regressed in the window: p50 200 ms against a 100 ms mean.
                if i < 3:
                    db.add(_case(run.id, "test_regressed", "slowsuite", 200, fp=f"fpreg{tag}"))
                w.history_runs.append(run)
            db.add(PerfBaseline(project_id=p1, test_fingerprint=f"fpreg{tag}", test_name="test_regressed",
                                suite_name="slowsuite", sample_count=12, mean_ms=100.0, m2=0.0, stddev_ms=5.0,
                                p95_ms=110.0, last_observed_ms=200, last_observed_at=NOW - timedelta(hours=3)))

            # 4. Project 2: the same suite key and fingerprint, enormous figures -- they must never appear in p1.
            leak = _run(p2, f"leak-{tag}", created_at=NOW - timedelta(hours=1), wall_clock_ms=9_999_999,
                        duration_ms=9_999_999, timing_source="report", primary_release_id=r2)
            db.add(leak)
            await db.flush()
            db.add(_suite_row(p2, leak, "SlowSuite", 9_999_999, 9_999_999))
            db.add(_case(leak.id, "test_regressed", "slowsuite", 9_999_999, fp=f"fpreg{tag}"))
            await db.commit()
            w.par, w.old = par, old

        # EXEC-02 writes the parallel and old runs' suite rows and wall clock, as finalize would.
        async with sessions() as db:
            await upsert_for_run(db, w.par.id, None)
            await upsert_for_run(db, w.old.id, None)
            await db.commit()

        def _jwt(uid):
            return {"Authorization": f"Bearer {create_access_token(str(uid))}"}

        w.admin, w.member, w.outsider = _jwt(admin), _jwt(member), _jwt(outsider)
        w.sessions = sessions
        w.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            yield w
        finally:
            await w.client.aclose()
    finally:
        app.dependency_overrides.pop(get_db, None)
        for statement in (
            text("DELETE FROM projects WHERE id IN (:a, :b)"),
            text("DELETE FROM access_audit_logs WHERE actor_user_id IN (:u1, :u2, :u3)"),
            delete(User).where(User.email.like(f"exec03-%-{tag}@example.com")),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement, {"a": p1, "b": p2, "u1": admin, "u2": member, "u3": outsider})
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"exec03 teardown: {type(exc).__name__}: {str(exc)[:160]}")
        patch.undo()
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _get(world, path, headers, **params):
    return await world.client.get(path, params=params, headers=headers)


# ── 1. Run timing ────────────────────────────────────────────────────────────


async def test_a_two_worker_run_shows_its_wall_clock_lanes_and_timeline(world):
    resp = await _get(world, f"/api/v1/runs/{world.par.id}/timing", world.member)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    run = body["run"]
    assert run["wall_clock_ms"] == 9000 and run["summed_ms"] == 17400 and run["parallelism"] == 1.93
    assert run["timing_source"] == "derived" and run["timed_count"] == 4

    lanes = body["workers"]["lanes"]
    assert body["workers"]["measured"] is True
    assert [(lane["worker_id"], lane["test_count"], lane["busy_ms"], lane["span_ms"]) for lane in lanes] == [
        ("0", 2, 9000, 9000), ("1", 2, 8400, 8400),
    ]
    timeline = body["timeline"]
    assert timeline["measured"] is True and timeline["total"] == 4 and timeline["truncated"] is False
    assert [item["offset_ms"] for item in timeline["items"]] == [0, 0, 3000, 4500]
    assert [item["test_name"] for item in timeline["items"]][2] == "test_refund"

    slowest = body["slowest"]["items"]
    assert slowest[0]["test_name"] == "test_refund" and slowest[0]["status"] == "FAILED"
    # p1's baseline, not p2's (same fingerprint, mean 1.0).
    assert slowest[0]["baseline"]["mean_ms"] == 4000.0 and slowest[0]["baseline_ratio"] == 1.35
    assert body["phases"]["measured"] is True and body["phases"]["setup_ms"] == 120
    suites = {item["suite_key"]: item for item in body["suites"]["items"]}
    assert suites["checkout"]["wall_ms"] == 9000 and suites["payments"]["wall_ms"] == 8400
    assert suites["payments"]["summed_ms"] == 8400 and suites["payments"]["source"] == "derived"
    assert body["duration_findings"]["run_id"] == str(world.par.id)


async def test_a_capped_timeline_keeps_the_longest_tests(world):
    resp = await _get(world, f"/api/v1/runs/{world.par.id}/timing", world.member, timeline_limit=2, slowest=1)
    body = resp.json()
    assert body["timeline"]["truncated"] is True and body["timeline"]["total"] == 4
    assert sorted(item["duration_ms"] for item in body["timeline"]["items"]) == [4500, 5400]
    assert len(body["slowest"]["items"]) == 1


async def test_an_old_run_has_summed_time_only_and_says_why(world):
    body = (await _get(world, f"/api/v1/runs/{world.old.id}/timing", world.member)).json()
    assert body["run"]["timing_source"] == "derived_sum" and body["run"]["wall_clock_ms"] is None
    assert body["run"]["parallelism"] is None and body["run"]["summed_ms"] == 6000
    assert body["timeline"] == {**body["timeline"], "measured": False, "reason": "report carried no timestamps",
                                "items": []}
    assert body["workers"]["measured"] is False and body["phases"]["measured"] is False
    assert [item["duration_ms"] for item in body["slowest"]["items"]] == [3000, 2000, 1000]
    assert body["suites"]["items"][0]["source"] == "derived_sum"
    assert body["suites"]["items"][0]["wall_ms"] is None


async def test_a_run_of_another_project_is_404(world):
    resp = await _get(world, f"/api/v1/runs/{world.par.id}/timing", world.outsider)
    assert resp.status_code == 404 and "test_refund" not in resp.text
    assert (await _get(world, f"/api/v1/runs/{world.par.id}/timing", world.admin)).status_code == 200
    assert (await _get(world, f"/api/v1/runs/{uuid.uuid4()}/timing", world.admin)).status_code == 404


# ── 2. The index ─────────────────────────────────────────────────────────────


async def test_the_slowest_read_uses_the_run_duration_index(world):
    from app.services import execution_metrics_service as svc

    sql = str(svc._SLOWEST_SQL).replace(":run_id", f"'{world.par.id}'::uuid").replace(":limit", "20")
    async with world.sessions() as db:
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(row[0] for row in (await db.execute(text("EXPLAIN " + sql))).all())
        await db.rollback()
    assert "ix_test_cases_run_duration" in plan, plan


# ── 3. Suite history ─────────────────────────────────────────────────────────


async def test_suite_history_is_one_suite_of_one_project_oldest_first(world):
    path = "/api/v1/analytics/execution/suite-history"
    resp = await _get(world, path, world.member, project_id=str(world.p1), suite_name=" slowsuite ", branch="main")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["suite_key"] == "slowsuite" and body["suite_name"] == "SlowSuite" and body["branch"] == "main"
    assert body["total"] == 25 and len(body["points"]) == 25 and body["truncated"] is False
    created = [point["run_created_at"] for point in body["points"]]
    assert created == sorted(created)
    assert [point["wall_ms"] for point in body["points"]][-5:] == [2000] * 5
    assert body["stats"]["wall"]["n"] == 25 and body["stats"]["wall"]["p50_ms"] == 1000.0
    assert 9_999_999 not in [point["wall_ms"] for point in body["points"]]  # p2's row
    assert body["meta"]["measured"] is True

    every_branch = (await _get(world, path, world.member, project_id=str(world.p1), suite_name="SlowSuite")).json()
    assert every_branch["total"] == 50 and every_branch["branch"] is None

    capped = (await _get(world, path, world.member, project_id=str(world.p1), suite_name="SlowSuite",
                         branch="main", limit=10)).json()
    assert capped["truncated"] is True and capped["total"] == 25 and len(capped["points"]) == 10
    # The NEWEST ten, oldest first; the percentiles still cover all 25.
    assert [p["wall_ms"] for p in capped["points"]] == [1000] * 5 + [2000] * 5
    assert capped["stats"]["wall"]["n"] == 25


async def test_suite_history_refusals(world):
    path = "/api/v1/analytics/execution/suite-history"
    missing = await _get(world, path, world.member, project_id=str(world.p1))
    assert missing.status_code == 422 and missing.json()["code"] == "missing_parameter"
    two = await world.client.get(path, params=[("project_id", str(world.p1)), ("suite_name", "a"),
                                               ("suite_name", "b")], headers=world.member)
    assert two.status_code == 422 and two.json()["code"] == "suite_single_valued"
    no_project = await _get(world, path, world.member, suite_name="SlowSuite")
    assert no_project.status_code == 422 and no_project.json()["code"] == "missing_parameter"
    bad_limit = await _get(world, path, world.member, project_id=str(world.p1), suite_name="x", limit=0)
    assert bad_limit.status_code == 422 and bad_limit.json()["code"] == "limit_range"


async def test_suite_history_of_a_project_the_caller_cannot_read_is_the_denied_shape(world):
    path = "/api/v1/analytics/execution/suite-history"
    resp = await _get(world, path, world.outsider, project_id=str(world.p1), suite_name="SlowSuite")
    assert resp.status_code == 200
    body = resp.json()
    assert body["points"] == [] and body["measured"] is False and body["project_id"] is None
    assert str(world.p1) not in resp.text and "exec03-p1" not in resp.text
    assert body["meta"]["measured"] is False


async def test_a_release_of_another_project_is_refused_before_any_data(world):
    for path, extra in (
        ("/api/v1/analytics/execution/suite-history", {"suite_name": "SlowSuite"}),
        ("/api/v1/analytics/execution-metrics", {}),
    ):
        resp = await _get(world, path, world.member, project_id=str(world.p1), release_id=str(world.r2), **extra)
        assert resp.status_code in (403, 404), (path, resp.status_code, resp.text)
        assert "9999999" not in resp.text


# ── 3b. Execution metrics ────────────────────────────────────────────────────


async def test_execution_metrics_of_the_project(world):
    resp = await _get(world, "/api/v1/analytics/execution-metrics", world.member, project_id=str(world.p1), days=30)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["measured"] is True and body["per_day"]["grain"] == "run"
    assert len(body["per_day"]["points"]) == 30
    assert body["totals"]["runs"] == 52  # par + old + 25 main + 25 dev; p2's run is not here
    assert body["totals"]["wall_clock_ms"] == 9000 + 5 * 2000 + 20 * 1000 + 25 * 1000

    suites = {item["suite_key"]: item for item in body["suites"]["items"]}
    assert {"slowsuite", "fewsuite", "checkout", "payments", "legacy"} <= set(suites)
    assert suites["slowsuite"]["current"]["runs"] == 50
    assert suites["slowsuite"]["current"]["wall"]["p95_ms"] < 9_999_999
    assert suites["legacy"]["current"]["wall"]["measured"] is False  # derived_sum: no wall clock, not 0

    found = body["regressions"]["suites"]
    assert found["measured"] is True
    assert [(i["suite_key"], i["branch"], i["ratio"]) for i in found["items"]] == [("slowsuite", "main", 2.0)]
    tests = body["regressions"]["tests"]
    assert [(i["test_fingerprint"], i["ratio"]) for i in tests["items"]] == [(f"fpreg{world.tag}", 2.0)]
    assert tests["items"][0]["window_n"] == 3  # p2's 9 999 999 ms execution is not in the window p50

    slowest = body["slowest_tests"]["items"]
    assert slowest[0]["test_name"] == "test_refund" and slowest[0]["p95_ms"] == 5400.0
    histogram = body["duration_histogram"]
    assert histogram["measured"] is True and histogram["n"] == 4 + 3 + 3
    assert sum(b["count"] for b in histogram["buckets"]) == histogram["n"]
    assert histogram["buckets"][-1]["count"] == 0  # p2's 9 999 999 ms test is not here


async def test_execution_metrics_filters(world):
    path = "/api/v1/analytics/execution-metrics"
    dev = (await _get(world, path, world.member, project_id=str(world.p1), branch="dev")).json()
    assert dev["totals"]["runs"] == 25 and dev["regressions"]["suites"]["items"] == []
    few = (await _get(world, path, world.member, project_id=str(world.p1), suite_name="FewSuite")).json()
    assert few["per_day"]["grain"] == "suite" and few["totals"]["runs"] == 4
    assert few["regressions"]["suites"]["measured"] is False
    assert few["regressions"]["suites"]["reason"] == "not enough history (4 of 15)"


async def test_execution_metrics_of_a_project_the_caller_cannot_read_is_the_denied_shape(world):
    resp = await _get(world, "/api/v1/analytics/execution-metrics", world.outsider, project_id=str(world.p1))
    assert resp.status_code == 200
    body = resp.json()
    assert body["measured"] is False and body["project_id"] is None
    assert body["suites"]["items"] == [] and body["slowest_tests"]["items"] == []
    assert body["totals"]["runs"] == 0 and "slowsuite" not in resp.text.lower()


# ── 5. Chart data ────────────────────────────────────────────────────────────


async def test_chart_data_wall_clock_is_run_grain_only(world):
    path = "/api/v1/analytics/chart-data"
    resp = await _get(world, path, world.member, project_id=str(world.p1), metric="wall_clock_p95",
                      group_by="branch")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["definitions"]["grain"] == "run_aggregate"
    points = {p["x"]: p for p in body["series"][0]["points"]}
    assert points["dev"]["y"] == 1000 and points["dev"]["n"] == 25
    refused = await _get(world, path, world.member, project_id=str(world.p1), metric="wall_clock_total",
                         group_by="suite")
    assert refused.status_code == 422 and refused.json()["code"] == "run_only_metric"


# ── 6. Test history ──────────────────────────────────────────────────────────


async def test_test_history_points_carry_their_timing_and_the_baseline(world):
    base = f"/api/v1/runs/{world.par.id}/tests"
    body = (await _get(world, f"{base}/{world.t_setup.id}/history", world.member)).json()
    point = body["history"][0]
    assert point["setup_ms"] == 120 and point["call_ms"] == 4300 and point["worker_id"] == "0"
    assert point["started_at"] is not None and point["timing_source"] == "report"
    assert body["duration_stats"]["measured"] is True and body["duration_stats"]["p50_ms"] == 4500.0
    assert body["baseline"] is None  # no baseline for this test

    slow = (await _get(world, f"{base}/{world.t_slow.id}/history", world.member)).json()
    assert slow["baseline"]["mean_ms"] == 4000.0 and slow["baseline"]["sample_count"] == 14
    outsider = await _get(world, f"{base}/{world.t_slow.id}/history", world.outsider)
    assert outsider.status_code == 404
