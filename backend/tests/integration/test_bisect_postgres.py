"""W23b FAIL-12 — the bisect narrowing's ONE statement against a real PostgreSQL (contract §2.1 B-1, §6.3 G-B).

What a statement-routing fake cannot show:

1. **The walk on real rows.** A six-run branch history (pass, pass, the test
   absent from two middle runs -- one of them on a case variant of the branch
   --, fail, fail) plus an IN_PROGRESS run and a run newer than the pivot on the
   same branch (both ignored) -> good = the newest pass, bad = the oldest fail,
   the two absent runs untested, newest first. ONE statement is executed.
2. **Scoping.** Another project's runs on the same branch with the same
   fingerprint (a fingerprint is md5(test_name:class_name), not project-unique)
   -- one of them a PASS between good and bad -- are never read.
3. **The main fallback.** A branch that never passed falls back to the newest
   ``main`` / ``master`` pass OLDER than bad (a newer main pass is not used).
4. **The bound.** 201 runs seeded with ONE ``INSERT ... SELECT generate_series``:
   the pass on the oldest is the 201st run, so the answer is
   ``no_pass_within_bound`` with ``runs_read`` 200 -- never "never passed".
5. **The plan.** ``EXPLAIN`` under ``SET LOCAL enable_seqscan = off`` names
   ``ix_test_runs_project_branch_created`` (migration 0245) in BOTH parts.
6. **The route end to end, OFFLINE,** through the ASGI app with a VIEWER
   member's token: the answer, the same 404 for another project's fingerprint,
   and no ``run_commit_ranges`` row written (a GET never lazily resolves).

The fixture awaits ``dispose_engine_for_loop()`` before building its engine and
after disposing it. ``AI_OFFLINE_MODE`` is patched True, so the bisect path
reads no feature flag and touches no Redis; the teardown closes Redis ONLY if
the ASGI request path bound the pool, and never touches Mongo. Every instant
is built inside its test; every row lives under fresh projects removed at the
end. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (= ``DATABASE_URL``) at head
(0245). To be listed in the ``postgres-integration`` job of
``.github/workflows/ci.yml`` after ``test_migration_0245_postgres.py``
(contract §6.4).
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

INDEX = "ix_test_runs_project_branch_created"
BRANCH = "feature/sentinel-pay"
TEST_NAME = "checkout_sentinel_test"
FP = hashlib.md5(f"{TEST_NAME}:payments.authorize.AuthorizeTest".encode()).hexdigest()


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    if not os.environ.get("DATABASE_URL", "").strip():
        pytest.skip("DATABASE_URL must point the app at the same database")
    return value


@pytest_asyncio.fixture(loop_scope="module")
async def env():
    from app.db import postgres as app_postgres
    from app.db import redis_client

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    made = SimpleNamespace(projects=[], users=[])
    try:
        yield SimpleNamespace(engine=engine, sessions=sessions, made=made)
    finally:
        async with sessions() as db:
            if made.projects:
                await db.execute(text("DELETE FROM projects WHERE id = ANY(:p)"), {"p": made.projects})
            if made.users:
                await db.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": made.users})
            await db.commit()
        await engine.dispose()
        if redis_client._pool is not None:  # only if the ASGI request path bound it
            await redis_client.close_redis()
        await app_postgres.dispose_engine_for_loop()


async def _project(env, tag: str) -> uuid.UUID:
    from app.models.postgres import Project

    pid = uuid.uuid4()
    async with env.sessions() as db:
        db.add(Project(id=pid, name=f"gb-bisect-{tag}-{pid.hex[:6]}", slug=f"gb-bisect-{tag}-{pid.hex[:6]}",
                       is_active=True))
        await db.commit()
    env.made.projects.append(pid)
    return pid


async def _runs(env, pid, specs):
    """``specs``: (build, branch, created_at, commit, run status, test status or None for no row)."""
    from app.models.postgres import TestCase, TestRun

    ids = []
    async with env.sessions() as db:
        for build, branch, at, commit, run_status, test_status in specs:
            run = TestRun(id=uuid.uuid4(), project_id=pid, build_number=build, jenkins_job="gb", status=run_status,
                          branch=branch, commit_hash=commit, ci_repo="acme/payments")
            run.created_at = at
            db.add(run)
            ids.append(run.id)
            if test_status is not None:
                await db.flush()
                db.add(TestCase(id=uuid.uuid4(), test_run_id=run.id, test_name=TEST_NAME,
                                class_name="payments.authorize.AuthorizeTest", test_fingerprint=FP,
                                status=test_status, suite_name="checkout"))
        await db.commit()
    return ids


async def _load(env, run_id):
    from sqlalchemy import select

    from app.models.postgres import TestRun

    async with env.sessions() as db:
        return (await db.execute(select(TestRun).where(TestRun.id == run_id))).scalar_one()


def _count_statements(engine):
    seen: list[str] = []

    def before(_conn, _cursor, statement, _params, _context, _many):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", before)
    return seen, lambda: event.remove(engine.sync_engine, "before_cursor_execute", before)


async def _branch_world(env):
    """P: the six-run history plus the runs the statement must skip; P2: the same test, the same branch."""
    now = datetime.now(timezone.utc)
    h = timedelta(hours=1)
    pid, other = await _project(env, "branch"), await _project(env, "other")
    ids = await _runs(env, pid, [
        ("101", BRANCH, now - 6 * h, "c1sentinel", "PASSED", "PASSED"),
        ("102", BRANCH, now - 5 * h, "goodsentinel01", "PASSED", "PASSED"),
        ("103", BRANCH, now - 4 * h, "c3sentinel", "PASSED", None),
        ("104", "Feature/Sentinel-Pay", now - 3 * h, "c4sentinel", "PASSED", None),
        ("105", BRANCH, now - 2 * h, "badsentinel002", "FAILED", "FAILED"),
        ("106", BRANCH, now - 1 * h, "c6sentinel", "FAILED", "FAILED"),
        ("106-live", BRANCH, now - 1.5 * h, None, "IN_PROGRESS", "PASSED"),  # still streaming: not read
        ("107", BRANCH, now, "c7sentinel", "PASSED", "PASSED"),               # newer than the pivot
        ("m1", "main", now - 2.5 * h, "mainsentinel", "PASSED", "PASSED"),   # a series pass exists: unused
    ])
    await _runs(env, other, [("201", BRANCH, now - 2.5 * h, "othersentinel", "PASSED", "PASSED")])
    return SimpleNamespace(pid=pid, other=other, ids=dict(zip(
        ("101", "102", "103", "104", "105", "106", "106-live", "107", "m1"), ids)))


async def test_the_walk_on_real_rows_in_one_statement(env):
    from app.services import bisect_service

    w = await _branch_world(env)
    run = await _load(env, w.ids["106"])
    seen, stop = _count_statements(env.engine)
    try:
        async with env.sessions() as db:
            n = await bisect_service.narrow_range_for_test(db, run, FP)
    finally:
        stop()
    assert len(seen) == 1, seen  # ONE statement
    assert (n.state, n.anchor, n.fallback_branch) == ("narrowed", "same_branch", None)
    assert n.good.run_id == w.ids["102"] and n.good.commit_hash == "goodsentinel01"
    assert n.bad.run_id == w.ids["105"] and n.streak_length == 2
    assert [r.run_id for r in n.untested] == [w.ids["104"], w.ids["103"]]  # newest first
    assert (n.untested_count, n.runs_read, n.bounded) == (2, 6, False)  # 106-live, 107, m1 and P2 not read


async def test_the_main_fallback_takes_the_newest_older_pass(env):
    from app.services import bisect_service

    now = datetime.now(timezone.utc)
    h = timedelta(hours=1)
    pid = await _project(env, "fallback")
    ids = await _runs(env, pid, [
        ("n1", "feature/never-green", now - 3 * h, "ngsentinel1", "FAILED", "FAILED"),
        ("n2", "feature/never-green", now - 1 * h, "ngsentinel2", "FAILED", "FAILED"),
        ("main-newer", "main", now - 2 * h, "mainsentinel2", "PASSED", "PASSED"),
        ("master-older", "master", now - 4 * h, "mastersentinel", "PASSED", "PASSED"),
        ("main-oldest", "main", now - 5 * h, "mainsentinel5", "PASSED", "PASSED"),
        ("main-failed", "main", now - 3.5 * h, "mainsentinel3", "FAILED", "FAILED"),
    ])
    run = await _load(env, ids[1])
    async with env.sessions() as db:
        n = await bisect_service.narrow_range_for_test(db, run, FP)
    assert (n.state, n.anchor, n.fallback_branch) == ("narrowed", "main_fallback", "master")
    assert n.good.run_id == ids[3] and n.bad.run_id == ids[0]
    assert n.untested == () and n.runs_read == 2
    # A run ON main has no fallback: it walks main only.
    main_run = await _load(env, ids[2])
    async with env.sessions() as db:
        m = await bisect_service.narrow_range_for_test(db, main_run, FP)
    assert m.state == "not_failing"


async def _bulk_world(env):
    """201 runs of one branch in ONE statement; the newest failed the test, the oldest passed it."""
    pid = await _project(env, "bulk")
    start = datetime.now(timezone.utc) - timedelta(days=1)
    async with env.sessions() as db:
        await db.execute(text(
            "INSERT INTO test_runs (id, project_id, build_number, jenkins_job, status, branch, commit_hash, created_at) "
            "SELECT gen_random_uuid(), :pid, 'bulk-' || g, 'gb', 'FAILED', :branch, 'bulksentinel' || g, "
            "       CAST(:start AS timestamptz) + g * interval '1 minute' "
            "FROM generate_series(1, 201) AS g"
        ), {"pid": pid, "branch": BRANCH, "start": start})
        await db.execute(text(
            "INSERT INTO test_cases (id, test_run_id, test_name, test_fingerprint, status) "
            "SELECT gen_random_uuid(), r.id, :name, :fp, "
            "       CASE WHEN r.build_number = 'bulk-201' THEN 'FAILED' ELSE 'PASSED' END "
            "FROM test_runs r WHERE r.project_id = :pid AND r.build_number IN ('bulk-1', 'bulk-201')"
        ), {"pid": pid, "name": TEST_NAME, "fp": FP})
        # Some main / master runs (the test never ran there): rows for the fallback part to walk.
        await db.execute(text(
            "INSERT INTO test_runs (id, project_id, build_number, jenkins_job, status, branch, created_at) "
            "SELECT gen_random_uuid(), :pid, 'trunk-' || g, 'gb', 'PASSED', "
            "       CASE WHEN g % 2 = 0 THEN 'main' ELSE 'master' END, "
            "       CAST(:start AS timestamptz) - g * interval '1 minute' "
            "FROM generate_series(1, 40) AS g"
        ), {"pid": pid, "start": start})
        newest = (await db.execute(text(
            "SELECT id FROM test_runs WHERE project_id = :pid AND build_number = 'bulk-201'"), {"pid": pid})).scalar_one()
        await db.commit()
    return pid, newest


async def test_the_200_run_bound_is_no_pass_within_bound(env):
    from app.services import bisect_service

    _pid, newest = await _bulk_world(env)
    run = await _load(env, newest)
    async with env.sessions() as db:
        n = await bisect_service.narrow_range_for_test(db, run, FP)
    assert (n.state, n.runs_read, n.bounded) == ("no_pass_within_bound", 200, True)
    assert n.good is None and n.bad.run_id == newest


def _append_children(plan: dict) -> list[dict]:
    if plan.get("Node Type") == "Append":
        return list(plan.get("Plans") or [])
    for child in plan.get("Plans") or []:
        found = _append_children(child)
        if found:
            return found
    return []


async def test_the_plan_uses_the_series_index_for_both_parts(env):
    from app.services import bisect_service

    pid, newest = await _bulk_world(env)
    run = await _load(env, newest)
    async with env.sessions() as db:
        # A real project runs many branches: 1 500 runs on 50 other branches, so a
        # project-only index is unselective (with ONE branch the planner rightly
        # reads the whole project). Then fresh statistics (a just-seeded table
        # estimates every index at one row, a tie) and no sequential scan.
        await db.execute(text(
            "INSERT INTO test_runs (id, project_id, build_number, jenkins_job, status, branch, created_at) "
            "SELECT gen_random_uuid(), :pid, 'noise-' || g, 'gb', 'PASSED', 'feature/other-' || (g % 50), "
            "       CAST(:start AS timestamptz) - g * interval '1 second' "
            "FROM generate_series(1, 1500) AS g"
        ), {"pid": pid, "start": run.created_at})
        await db.commit()
        await db.execute(text("ANALYZE test_runs"))
        await db.execute(text("ANALYZE test_cases"))
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        raw = (await db.execute(text("EXPLAIN (FORMAT JSON) " + bisect_service._NARROW_SQL.text),
                                bisect_service.narrow_params(run, FP))).scalar_one()
        await db.rollback()
    plan = (json.loads(raw) if isinstance(raw, str) else raw)[0]["Plan"]
    parts = _append_children(plan)
    assert len(parts) == 2, json.dumps(plan)[:2000]
    for part in parts:
        assert INDEX in json.dumps(part), json.dumps(part)[:2000]


async def test_the_route_end_to_end_offline(env, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.core.config import settings
    from app.main import app
    from app.models.postgres import ProjectMember, User, UserRole
    from app.services.auth_session_tokens import issue_access_jwt

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    w = await _branch_world(env)
    other_fp = hashlib.md5(b"refund_sentinel_test:payments.refund.RefundTest").hexdigest()
    other_run = (await _runs(env, w.other, [("202", BRANCH, datetime.now(timezone.utc) - timedelta(hours=3),
                                             "othersentinel2", "FAILED", None)]))[0]
    viewer = uuid.uuid4()
    async with env.sessions() as db:
        db.add(User(id=viewer, username=f"gb-viewer-{viewer.hex[:8]}", email=f"gb-{viewer.hex[:8]}@example.test",
                    hashed_password="!unusable", role=UserRole.VIEWER, is_active=True))
        await db.flush()
        db.add(ProjectMember(project_id=w.pid, user_id=viewer, role=UserRole.VIEWER))
        await db.execute(text(
            "INSERT INTO test_cases (id, test_run_id, test_name, test_fingerprint, status) "
            "VALUES (gen_random_uuid(), :run, 'refund_sentinel_test', :fp, 'FAILED')"), {"run": other_run, "fp": other_fp})
        await db.commit()
        token = await issue_access_jwt(db, str(viewer), timedelta(minutes=10))
    env.made.users.append(viewer)
    headers = {"Authorization": f"Bearer {token}"}
    run_id = w.ids["106"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gb-bisect.test") as client:
        ok = await client.get(f"/api/v1/runs/{run_id}/bisect", params={"fingerprint": FP}, headers=headers)
        foreign = await client.get(f"/api/v1/runs/{run_id}/bisect", params={"fingerprint": other_fp},
                                   headers=headers)
        unknown = await client.get(f"/api/v1/runs/{run_id}/bisect", params={"fingerprint": "fp-sentinel-none"},
                                   headers=headers)
        missing = await client.get(f"/api/v1/runs/{run_id}/bisect", headers=headers)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert (body["state"], body["anchor"], body["series_branch"], body["test_name"]) == (
        "narrowed", "same_branch", BRANCH, TEST_NAME)
    assert (body["good"]["build_number"], body["good"]["commit_hash"]) == ("102", "goodsentinel01")
    assert (body["bad"]["build_number"], body["bad"]["commit_hash"]) == ("105", "badsentinel002")
    assert [r["build_number"] for r in body["untested_runs"]] == ["104", "103"]
    assert (body["compare"]["state"], body["compare"]["reason"]) == ("unavailable", "offline_mode")
    assert body["narrowed_from"] == "unavailable" and body["compare"]["run_level_commit_count"] is None
    expected_404 = {"detail": {"code": "fingerprint_not_in_run", "message": "That test is not part of this run."}}
    assert (foreign.status_code, foreign.json()) == (404, expected_404)
    assert (unknown.status_code, unknown.json()) == (404, expected_404)
    assert missing.status_code == 422
    async with env.sessions() as db:
        written = (await db.execute(text("SELECT count(*) FROM run_commit_ranges WHERE run_id = :r"),
                                    {"r": run_id})).scalar_one()
    assert written == 0  # a GET never lazily resolves a range
