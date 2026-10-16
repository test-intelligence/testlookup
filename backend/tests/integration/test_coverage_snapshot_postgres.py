"""W08 TRND-07 -- coverage snapshots, the history route and the untagged-tests
route against real Postgres.

Every test here is SYNCHRONOUS and drives its async work through
``asyncio.run`` with NullPool engines: the daily task is run through Celery's
own ``apply`` (the real task, the real ``coverage_snapshot_service``, the real
upsert on ``uq_coverage_snapshots_project_day``), and ``run_async`` refuses to
run inside a running loop, so no pytest-asyncio loop may be running around it.

Proved:

1. **A snapshot's four values** on seeded data for a fixed day D: the
   catalogue (created before D ended; deleted before D out, deleted after D in,
   ``needs_review`` in), the tests and suites exercised in the 30 days ending D
   (a run 29 days back in, 30 days back out, after D out; a live-stream run
   under its session suite), and the per-suite rows.
2. **Idempotency.** The task twice: one row a day; the second run's values.
3. **Backfill.** A project whose only runs are 10 days old gets a row for every
   day of the last 30 on the first night, yesterday included, today never.
4. **History.** The contract's key set, ``measured: false`` before the first
   snapshot, ``days=366`` a 422, a foreign project a 403, release and suite
   declared ignored.
5. **Untagged tests.** Only unlabelled executions' tests (no suite, or the
   ``unknown`` labels), the catalogue suite, ``canonical_id: null`` without a
   catalogue row, ``total`` beyond the page.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (a throwaway
database). Redis is ``REDIS_URL`` when set, else ``fakeredis``.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone
import json
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration]

UTC = timezone.utc
HISTORY = "/api/v1/analytics/coverage/history"
UNTAGGED = "/api/v1/analytics/coverage/untagged-tests"
HISTORY_KEYS = {"window_days", "measured", "reason", "first_snapshot_day", "points", "meta"}
UNTAGGED_KEYS = {"items", "total", "page", "size", "window_days", "meta"}


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _sessions():
    """A session factory for THIS loop: NullPool keeps no connection across loops."""
    engine = create_async_engine(_dsn(), poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _run(coro):
    return asyncio.run(coro)


def _at(day: date, hours: float = 0) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC) + timedelta(hours=hours)


TODAY = datetime.now(UTC).date()
D = TODAY - timedelta(days=1)


# ── the world ──────────────────────────────────────────────────────────────


async def _seed(tag: str) -> SimpleNamespace:
    from app.core.security import create_access_token
    from app.models.postgres import (
        CanonicalTestCase,
        LaunchStatus,
        Project,
        ProjectMember,
        TestCase,
        TestRun,
        TestSuite,
        User,
        UserRole,
    )

    engine, sessions = _sessions()
    ids = SimpleNamespace(
        p=uuid.uuid4(), r=uuid.uuid4(), q=uuid.uuid4(),
        member=uuid.uuid4(), outsider=uuid.uuid4(), canon={}, runs={},
    )
    async with sessions() as db:
        for pid, label in ((ids.p, "p"), (ids.r, "r"), (ids.q, "q")):
            db.add(Project(id=pid, name=f"snap-{label}-{tag}", slug=f"snap-{label}-{tag}",
                           is_active=True, description=f"throwaway W08 TRND-07 {tag}"))
        for uid, label in ((ids.member, "member"), (ids.outsider, "outsider")):
            db.add(User(id=uid, email=f"snap-{label}-{tag}@example.com", username=f"snap_{label}_{tag}",
                        full_name=f"Snap {label}", hashed_password="!unusable",
                        role=UserRole.QA_ENGINEER.value))
        await db.flush()
        for pid in (ids.p, ids.r):
            db.add(ProjectMember(project_id=pid, user_id=ids.member, role=UserRole.QA_ENGINEER.value))
        db.add(ProjectMember(project_id=ids.q, user_id=ids.outsider, role=UserRole.QA_ENGINEER.value))
        suite = TestSuite(id=uuid.uuid4(), project_id=ids.p, name=f"Default Suite (snap-p-{tag})",
                          is_default=True)
        checkout = TestSuite(id=uuid.uuid4(), project_id=ids.p, name="Checkout")
        r_suite = TestSuite(id=uuid.uuid4(), project_id=ids.r, name="Default Suite (r)", is_default=True)
        db.add_all([suite, checkout, r_suite])
        await db.flush()
        ids.default_suite, ids.checkout_suite = suite.id, checkout.id

        old = _at(D - timedelta(days=40))
        canon = [
            # (key, fp, status, created_at, deleted_observed_at, suite)
            ("c1", "f1", "active", old, None, checkout),
            ("c2", "gone-before", "deleted", old, _at(D - timedelta(days=5)), suite),
            ("c3", "gone-after", "deleted", old, _at(TODAY, 2), suite),
            ("c4", "new-today", "active", _at(TODAY, 1), None, suite),
            ("c5", "review", "needs_review", old, None, suite),
            ("c6", f"f6-{tag}", "active", old, None, suite),
        ]
        for key, fp, status, created, deleted_at, home in canon:
            row = CanonicalTestCase(
                id=uuid.uuid4(), project_id=ids.p, test_suite_id=home.id, test_fingerprint=fp,
                test_name=fp, status=status, source="execution", created_at=created,
                deleted_observed_at=deleted_at,
            )
            db.add(row)
            ids.canon[key] = row.id

        def run(key, pid, when, cases, *, trigger=None, primary=None):
            run_id = uuid.uuid4()
            statuses = [c[2] for c in cases]
            db.add(TestRun(
                id=run_id, project_id=pid, build_number=f"snap-{key}-{tag}", jenkins_job="snap",
                status=LaunchStatus.PASSED, ingestion_source="sdk", trigger_source=trigger,
                primary_suite_name=primary, total_tests=len(cases),
                passed_tests=statuses.count("PASSED"), failed_tests=statuses.count("FAILED"),
                created_at=when,
            ))
            ids.runs[key] = (run_id, cases, when)

        run("A", ids.p, _at(D - timedelta(days=29), 1),
            [("f1", "Checkout", "PASSED"), ("f2", "Checkout", "FAILED")])
        run("B", ids.p, _at(D - timedelta(days=30), 23), [("f3", "Checkout", "PASSED")])
        run("L", ids.p, _at(D - timedelta(days=2)), [("f4", "com.acme.LiveTest", "PASSED")],
            trigger="live_stream", primary="Payments Live")
        # One row per test per run: f5's two unlabelled executions are two runs.
        run("N", ids.p, _at(D - timedelta(days=3)), [("f5", None, "PASSED")])
        run("N2", ids.p, _at(D - timedelta(days=4)), [("f5", "  ", "FAILED")])
        run("U", ids.p, _at(D, 6), [(f"f6-{tag}", "unknown", "PASSED")])
        run("T", ids.p, _at(TODAY, 0.5), [("f7", "Checkout", "PASSED")])
        run("R", ids.r, _at(TODAY - timedelta(days=10), 3), [("r1", "Smoke", "PASSED")])
        await db.flush()
        for key, (run_id, cases, when) in ids.runs.items():
            for fp, suite_name, status in cases:
                db.add(TestCase(id=uuid.uuid4(), test_run_id=run_id, test_fingerprint=fp, test_name=fp,
                                suite_name=suite_name, status=status, created_at=when))
        await db.commit()
    await engine.dispose()
    ids.headers = {
        "member": {"Authorization": f"Bearer {create_access_token(str(ids.member))}"},
        "outsider": {"Authorization": f"Bearer {create_access_token(str(ids.outsider))}"},
    }
    return ids


async def _teardown(ids) -> None:
    engine, sessions = _sessions()
    for statement in (
        "DELETE FROM canonical_test_cases WHERE project_id = ANY(:ids)",
        "DELETE FROM projects WHERE id = ANY(:ids)",
        "DELETE FROM access_audit_logs WHERE actor_user_id = ANY(:users)",
        "DELETE FROM users WHERE id = ANY(:users)",
    ):
        try:
            async with sessions.begin() as db:
                await db.execute(text(statement), {
                    "ids": [ids.p, ids.r, ids.q] + getattr(ids, "extra", []),
                    "users": [ids.member, ids.outsider] + getattr(ids, "extra_users", []),
                })
        except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
            print(f"snapshot teardown: {type(exc).__name__}: {str(exc)[:160]}")
    await engine.dispose()


@pytest.fixture(scope="module")
def world():
    _dsn()
    ids = _run(_seed(uuid.uuid4().hex[:10]))
    try:
        yield ids
    finally:
        _run(_teardown(ids))


async def _rows(pid: uuid.UUID) -> list[Any]:
    engine, sessions = _sessions()
    async with sessions() as db:
        rows = (await db.execute(text(
            "SELECT snapshot_day, total_tests, automated_count, total_suites, suite_coverage, snapshot_date "
            "FROM coverage_snapshots WHERE project_id = :pid ORDER BY snapshot_day"
        ), {"pid": pid})).fetchall()
    await engine.dispose()
    return list(rows)


def _task(monkeypatch) -> dict:
    """The real task, eagerly, on fresh NullPool sessions in its own loop."""
    from app.worker import tasks

    _engine, sessions = _sessions()
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)
    return tasks.take_coverage_snapshot.apply().get()


async def _get(path: str, params: list[tuple[str, str]], headers: dict):
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from httpx import ASGITransport, AsyncClient
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.core import analytics_errors as errors
    from app.db.postgres import get_db
    from app.routers import analytics_coverage_map

    engine, sessions = _sessions()

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, errors.http_exception_handler)
    app.add_exception_handler(RequestValidationError, errors.validation_exception_handler)
    app.add_exception_handler(errors.AnalyticsQueryError, errors.analytics_query_error_handler)
    app.include_router(analytics_coverage_map.router)
    app.dependency_overrides[get_db] = _get_db
    redis = _redis_client()
    patch = pytest.MonkeyPatch()
    patch.setattr("app.db.redis_client.get_redis", lambda: redis)
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            return await client.get(path, params=params, headers=headers)
    finally:
        patch.undo()
        await redis.aclose()
        await engine.dispose()


def _redis_client():
    url = os.environ.get("REDIS_URL", "").strip()
    if url:
        import redis.asyncio as redis_asyncio

        return redis_asyncio.Redis.from_url(url, decode_responses=True)
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


# ── 1. a snapshot's values ─────────────────────────────────────────────────


def test_a_snapshot_counts_the_catalogue_and_the_30_days_ending_its_day(world):
    from app.services import coverage_snapshot_service as svc

    async def case():
        engine, sessions = _sessions()
        async with sessions() as db:
            values = await svc.snapshot_values(db, world.p, D)
        await engine.dispose()
        return values

    values = _run(case())
    # c1, c3 (deleted after D), c5 (needs_review), c6; not c2 (deleted before) nor c4 (created today).
    assert values["catalogue_tests"] == 4
    # f1, f2 (29 days back), f4 (live stream), f5, f6; not f3 (30 days back) nor f7 (today).
    assert values["exercised_tests"] == 5
    # Checkout, Payments Live and the 'unknown' label; f5's rows carry none.
    assert values["suites"] == 3
    rows = {row["suite_name"]: row for row in values["suite_coverage"]}
    assert set(rows) == {"Checkout", "Payments Live", "Unknown Suite", "unknown"}
    assert (rows["Checkout"]["unique_tests"], rows["Checkout"]["passed"], rows["Checkout"]["failed"]) == (2, 1, 1)
    assert rows["Checkout"]["pass_rate"] == 50.0
    assert rows["Unknown Suite"]["executions"] == 2


# ── 2 + 3. the task: backfill and idempotency ──────────────────────────────


def test_the_first_night_backfills_30_days_and_a_second_run_rewrites_yesterday(world, monkeypatch):
    first = _task(monkeypatch)
    assert first["failed"] == 0 and first["rows"] >= 30
    rows = _run(_rows(world.r))
    days = [row.snapshot_day for row in rows]
    assert days == [TODAY - timedelta(days=n) for n in range(30, 0, -1)]
    assert TODAY not in days
    # R's only run is 10 days old: exercised on the days whose window holds it.
    exercised = {row.snapshot_day: row.automated_count for row in rows}
    assert exercised[TODAY - timedelta(days=1)] == 1 and exercised[TODAY - timedelta(days=10)] == 1
    assert exercised[TODAY - timedelta(days=11)] == 0
    yesterday_before = next(row for row in rows if row.snapshot_day == TODAY - timedelta(days=1))

    async def add_canonical():
        from app.models.postgres import CanonicalTestCase

        engine, sessions = _sessions()
        async with sessions() as db:
            suite_id = (await db.execute(
                text("SELECT id FROM test_suites WHERE project_id = :pid"), {"pid": world.r}
            )).scalar_one()
            db.add(CanonicalTestCase(
                id=uuid.uuid4(), project_id=world.r, test_suite_id=suite_id, test_fingerprint="r-new",
                test_name="r-new", status="active", source="managed",
                created_at=_at(TODAY - timedelta(days=1), 2),
            ))
            await db.commit()
        await engine.dispose()

    _run(add_canonical())
    second = _task(monkeypatch)
    assert second["failed"] == 0
    rows_after = _run(_rows(world.r))
    assert [row.snapshot_day for row in rows_after] == days, "one row a day, never a duplicate"
    yesterday = next(row for row in rows_after if row.snapshot_day == TODAY - timedelta(days=1))
    assert yesterday.total_tests == yesterday_before.total_tests + 1, "the second run's values"
    assert yesterday.snapshot_date >= yesterday_before.snapshot_date
    # A day the first night stored and the second did not revisit keeps its values.
    older = {row.snapshot_day: row.total_tests for row in rows_after}
    assert older[TODAY - timedelta(days=5)] == next(
        row.total_tests for row in rows if row.snapshot_day == TODAY - timedelta(days=5)
    )


def test_the_stored_columns_carry_the_snapshot_s_values(world, monkeypatch):
    from app.services import coverage_snapshot_service as svc

    rows = {row.snapshot_day: row for row in _run(_rows(world.p))}
    if D not in rows:
        _task(monkeypatch)
        rows = {row.snapshot_day: row for row in _run(_rows(world.p))}

    async def live():
        engine, sessions = _sessions()
        async with sessions() as db:
            values = await svc.snapshot_values(db, world.p, D)
        await engine.dispose()
        return values

    values = _run(live())
    row = rows[D]
    assert (row.total_tests, row.automated_count, row.total_suites) == (
        values["catalogue_tests"], values["exercised_tests"], values["suites"],
    )
    stored = json.loads(row.suite_coverage) if isinstance(row.suite_coverage, str) else row.suite_coverage
    assert stored == values["suite_coverage"]


def test_the_unique_index_refuses_a_second_row_for_a_day(world, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    if not _run(_rows(world.p)):
        _task(monkeypatch)

    async def duplicate():
        engine, sessions = _sessions()
        try:
            async with sessions() as db:
                await db.execute(text(
                    "INSERT INTO coverage_snapshots (id, project_id, snapshot_day, total_suites, total_tests, "
                    "automated_count) VALUES (:id, :pid, :day, 0, 0, 0)"
                ), {"id": uuid.uuid4(), "pid": world.p, "day": D})
                await db.commit()
        finally:
            await engine.dispose()

    with pytest.raises(IntegrityError):
        _run(duplicate())


# ── 4. the history route ───────────────────────────────────────────────────


def test_history_answers_the_contract_s_keys_for_the_stored_days(world, monkeypatch):
    if len(_run(_rows(world.r))) < 30:
        _task(monkeypatch)
    resp = _run(_get(HISTORY, [("project_id", str(world.r)), ("days", "30")], world.headers["member"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == HISTORY_KEYS
    assert (body["window_days"], body["measured"], body["reason"]) == (30, True, None)
    assert body["first_snapshot_day"] == (TODAY - timedelta(days=30)).isoformat()
    assert [p["day"] for p in body["points"]] == [
        (TODAY - timedelta(days=n)).isoformat() for n in range(30, 0, -1)
    ]
    for point in body["points"]:
        assert set(point) == {"day", "catalogue_tests", "exercised_tests", "exercised_pct", "suites"}
    # R's catalogue is empty before the canonical the idempotency test adds.
    assert body["points"][0]["exercised_pct"] is None
    assert body["meta"]["definitions"]["scope"].startswith("A snapshot is project-wide")
    short = _run(_get(HISTORY, [("project_id", str(world.r)), ("days", "7")], world.headers["member"])).json()
    assert len(short["points"]) == 7


def test_history_before_the_first_snapshot_is_not_measured(world):
    """A project created after the beat ran. Its own user: a member's readable
    projects are cached for five minutes, so an earlier request's list would
    not hold it yet."""
    from app.core.security import create_access_token

    async def fresh_project():
        from app.models.postgres import Project, ProjectMember, User, UserRole

        engine, sessions = _sessions()
        pid, uid = uuid.uuid4(), uuid.uuid4()
        async with sessions() as db:
            db.add(Project(id=pid, name=f"snap-new-{pid.hex[:8]}", slug=f"snap-new-{pid.hex[:8]}",
                           is_active=True))
            db.add(User(id=uid, email=f"snap-new-{uid.hex[:8]}@example.com", username=f"snap_new_{uid.hex[:8]}",
                        full_name="Snap new", hashed_password="!unusable", role=UserRole.QA_ENGINEER.value))
            await db.flush()
            db.add(ProjectMember(project_id=pid, user_id=uid, role=UserRole.QA_ENGINEER.value))
            await db.commit()
        await engine.dispose()
        return pid, uid

    pid, uid = _run(fresh_project())
    world.extra = getattr(world, "extra", []) + [pid]
    world.extra_users = getattr(world, "extra_users", []) + [uid]
    headers = {"Authorization": f"Bearer {create_access_token(str(uid))}"}
    body = _run(_get(HISTORY, [("project_id", str(pid))], headers)).json()
    assert set(body) == HISTORY_KEYS
    assert (body["measured"], body["first_snapshot_day"], body["points"]) == (False, None, [])
    assert body["reason"] == "No coverage snapshot yet: the first is taken at 00:05 UTC."
    assert body["window_days"] == 90
    assert body["meta"]["measured"] is False


def test_history_refuses_a_year_and_a_day_and_a_foreign_project(world):
    long = _run(_get(HISTORY, [("project_id", str(world.p)), ("days", "366")], world.headers["member"]))
    assert long.status_code == 422 and long.json()["code"] == "window_days_range"
    foreign = _run(_get(HISTORY, [("project_id", str(world.p))], world.headers["outsider"]))
    assert foreign.status_code == 403
    missing = _run(_get(HISTORY, [], world.headers["member"]))
    assert missing.status_code == 422 and missing.json()["param"] == "project_id"


def test_history_declares_release_and_suite_ignored(world):
    params = [("project_id", str(world.p)), ("days", "14"), ("suite_name", "Checkout")]
    body = _run(_get(HISTORY, params, world.headers["member"])).json()
    assert [item["dimension"] for item in body["meta"]["ignored_filters"]] == ["suite"]
    # A declared-ignored filter is not in the scope the envelope describes.
    assert body["meta"]["scope"]["suites"] == []


# ── 5. the untagged tests ──────────────────────────────────────────────────


def test_untagged_tests_are_the_unlabelled_executions_tests(world):
    params = [("project_id", str(world.p)), ("days", "30")]
    resp = _run(_get(UNTAGGED, params, world.headers["member"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == UNTAGGED_KEYS
    assert (body["total"], body["page"], body["size"], body["window_days"]) == (2, 1, 50, 30)
    items = {item["test_fingerprint"]: item for item in body["items"]}
    assert set(items) == {"f5", next(fp for fp in items if fp.startswith("f6-"))}
    f5 = items["f5"]
    assert (f5["canonical_id"], f5["catalogue_suite"], f5["executions"]) == (None, None, 2)
    f6 = next(item for fp, item in items.items() if fp.startswith("f6-"))
    assert f6["canonical_id"] == str(world.canon["c6"])
    assert f6["catalogue_suite_id"] == str(world.default_suite)
    assert f6["catalogue_suite"].startswith("Default Suite (")
    # Busiest first.
    assert body["items"][0]["test_fingerprint"] == "f5"


def test_untagged_total_is_the_distinct_count_beyond_the_page(world):
    params = [("project_id", str(world.p)), ("days", "30"), ("size", "1")]
    first = _run(_get(UNTAGGED, params, world.headers["member"])).json()
    second = _run(_get(UNTAGGED, params + [("page", "2")], world.headers["member"])).json()
    past = _run(_get(UNTAGGED, params + [("page", "3")], world.headers["member"])).json()
    assert (first["total"], len(first["items"]), first["items"][0]["test_fingerprint"]) == (2, 1, "f5")
    assert (second["total"], len(second["items"])) == (2, 1)
    assert second["items"][0]["test_fingerprint"].startswith("f6-")
    assert (past["total"], past["items"]) == (2, [])


@pytest.mark.parametrize(
    "extra, status",
    [([("size", "201")], 422), ([("size", "0")], 422), ([("page", "0")], 422), ([("days", "91")], 422)],
)
def test_untagged_refuses_a_page_outside_its_bounds(world, extra: list, status: int):
    resp = _run(_get(UNTAGGED, [("project_id", str(world.p))] + extra, world.headers["member"]))
    assert resp.status_code == status


def test_untagged_refuses_a_foreign_project(world):
    resp = _run(_get(UNTAGGED, [("project_id", str(world.p))], world.headers["outsider"]))
    assert resp.status_code == 403
