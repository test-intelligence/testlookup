"""W36 STRAT-08 — "Likely affected tests" against a real PostgreSQL (contract §2, §5 G-B).

What the statement-evaluating fake in ``tests/test_likely_affected_tests_route.py``
cannot show:

1. **The ranked list and the co-failure counts on real rows**, against a hand
   count: a project whose 32 prior ranges (over 32 days, 33 distinct paths) make
   ``get_tia_readiness`` available; 8 of them share a changed path; the cart test
   failed in 4 of those 8 (and once in a run sharing nothing), the charge test
   was BROKEN in 1; a LATER run sharing the path, where cart also failed, is not
   prior and does not count.
2. **Project scoping.** A FOREIGN project with the identical fingerprints
   (md5(test_name:class_name) is not project-unique), the identical changed
   paths and those tests failing in every one of its runs -- plus its own flaky
   score for the cart test -- adds nothing.
3. **History not ready** on a thin project: the path-only list, co-failure null.
4. **Read-only, through the real app:** a VIEWER member's token gets the same
   answer as the service; a non-member gets the guard's 404; the row counts of
   every table the read touches (test_runs, test_cases, run_commit_ranges,
   flaky_score) are unchanged and no range row is written for a run without one.
5. **Timing (B5):** a run with 2,000 tests and 300 changed files over 30 prior
   ranges of 2,000 tests each; the elapsed time is printed (run with ``-s``),
   not asserted.

The fixture awaits ``dispose_engine_for_loop()`` before building its engine and
after disposing it; every row lives under fresh projects (and one user per
route test) removed at the end. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (=
``DATABASE_URL``) at head. To be listed in the ``postgres-integration`` job of
``.github/workflows/ci.yml``.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

CHANGED = ["payments/charge.py", "payments/refund.py", "docs/README.md"]
TESTS = {  # key -> (test_name, class_name)
    "charge": ("test_charge_succeeds", "payments.ChargeTest"),
    "refund": ("test_refund_issued", "billing.RefundTest"),
    "cart": ("test_cart_total", "cart.CartTest"),
    "ui": ("test_button_click", "ui.ButtonTest"),
}


def _fp(key: str) -> str:
    name, cls = TESTS[key]
    return hashlib.md5(f"{name}:{cls}".encode()).hexdigest()


FP = {key: _fp(key) for key in TESTS}


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
        yield SimpleNamespace(engine=engine, sessions=sessions, made=made, now=datetime.now(timezone.utc))
    finally:
        async with sessions() as db:  # cascades to runs, cases, ranges, flaky scores, members
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
    # Raw SQL: this read touches no project column, so the seed should not depend on the ORM's full column list.
    pid = uuid.uuid4()
    async with env.sessions() as db:
        await db.execute(text("INSERT INTO projects (id, name, slug, is_active) VALUES (:i, :n, :n, true)"),
                         {"i": pid, "n": f"gb-impact-{tag}-{pid.hex[:6]}"})
        await db.commit()
    env.made.projects.append(pid)
    return pid


async def _run(env, pid, at, commits=None, statuses=None, *, source="supplied", base="green_baseline"):
    """One run at ``at``; its range (``commits``: list of file lists, or None for no range); its test cases."""
    from app.models.postgres import RunCommitRange, TestCase, TestRun

    async with env.sessions() as db:
        run = TestRun(id=uuid.uuid4(), project_id=pid, build_number=f"b-{uuid.uuid4().hex[:8]}", jenkins_job="gb",
                      status="FAILED", commit_hash=None)
        run.created_at = at
        db.add(run)
        await db.flush()
        if commits is not None:
            db.add(RunCommitRange(run_id=run.id, project_id=pid, source=source, base_source=base,
                                  commits=[{"sha": f"{i:040x}", "author": "dev", "message": "m", "files": files}
                                           for i, files in enumerate(commits)],
                                  resolved_at=at + timedelta(minutes=5)))
        for key, status in (statuses or {}).items():
            name, cls = TESTS[key]
            db.add(TestCase(id=uuid.uuid4(), test_run_id=run.id, test_name=name, class_name=cls,
                            test_fingerprint=FP[key], status=status, suite_name="impact"))
        await db.commit()
        return run.id


async def _load(env, run_id):
    from app.models.postgres import TestRun

    async with env.sessions() as db:
        return (await db.execute(select(TestRun).where(TestRun.id == run_id))).scalar_one()


async def _flaky(env, pid, key, score, confidence):
    from app.models.postgres import FlakyScore

    async with env.sessions() as db:
        db.add(FlakyScore(project_id=pid, test_fingerprint=FP[key], score=score, confidence=confidence,
                          observation_count=40))
        await db.commit()


ALL_PASS = {"charge": "PASSED", "refund": "PASSED", "cart": "PASSED", "ui": "PASSED"}
VIEWED_RANGE = [["payments/charge.py", "payments/refund.py"], [], ["docs/README.md"]]  # 2 of 3 commits carry files


async def _rich_world(env):
    p1, p2 = await _project(env, "p1"), await _project(env, "p2")
    viewed_at = env.now - timedelta(hours=2)
    for i in range(32):  # prior P1 runs, one a day; every 4th shares payments/charge.py
        files = (["payments/charge.py"] if i % 4 == 0 else []) + [f"svc/mod_{i:02d}.py"]
        statuses = dict(ALL_PASS)
        if i % 8 == 0:
            statuses["cart"] = "FAILED"        # i = 0, 8, 16, 24 -- all sharing: cart counts 4
        if i == 1:
            statuses["cart"] = "FAILED"        # shares nothing: not counted
        if i == 4:
            statuses["charge"] = "BROKEN"      # sharing: charge counts 1
        if i == 2:
            statuses["charge"] = "FAILED"      # shares nothing
        if i in (3, 5, 7):
            statuses["refund"] = "FAILED"      # share nothing: refund counts 0
        await _run(env, p1, viewed_at - timedelta(days=i + 1), [files], statuses)
    viewed = await _run(env, p1, viewed_at, VIEWED_RANGE, {**ALL_PASS, "charge": "FAILED"})
    # A LATER run sharing the path is not prior: its cart failure does not count.
    await _run(env, p1, viewed_at + timedelta(minutes=30), [["payments/charge.py"]], {**ALL_PASS, "cart": "FAILED"})
    await _flaky(env, p1, "ui", 0.9, "high")       # qualifies, but ui has no evidence: not listed
    await _flaky(env, p1, "charge", 0.9, "low")    # a low band never qualifies
    # The FOREIGN project: identical paths and fingerprints, failing in every run; its own qualifying flaky cart.
    for d in range(10):
        await _run(env, p2, viewed_at - timedelta(days=d + 1), [list(CHANGED)],
                   {"charge": "FAILED", "refund": "FAILED", "cart": "FAILED"})
    await _flaky(env, p2, "cart", 0.95, "high")
    return SimpleNamespace(p1=p1, p2=p2, viewed=viewed)


async def _counts(env, pids):
    queries = {
        "test_runs": "SELECT count(*) FROM test_runs WHERE project_id = ANY(:p)",
        "test_cases": "SELECT count(*) FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id "
                      "WHERE tr.project_id = ANY(:p)",
        "run_commit_ranges": "SELECT count(*) FROM run_commit_ranges WHERE project_id = ANY(:p)",
        "flaky_score": "SELECT count(*) FROM flaky_score WHERE project_id = ANY(:p)",
    }
    async with env.sessions() as db:
        return {name: (await db.execute(text(sql), {"p": pids})).scalar_one() for name, sql in queries.items()}


def _capture(engine):
    seen: list[str] = []

    def before(_conn, _cursor, statement, _params, _context, _many):
        seen.append(statement)

    from sqlalchemy import event

    event.listen(engine.sync_engine, "before_cursor_execute", before)
    return seen, lambda: event.remove(engine.sync_engine, "before_cursor_execute", before)


def _by_key(body):
    reverse = {fp: key for key, fp in FP.items()}
    return {reverse[t["test_fingerprint"]]: t for t in body["tests"]}


async def test_the_ranked_list_and_co_failures_match_the_hand_count(env):
    from app.services import test_impact_service as tis

    w = await _rich_world(env)
    run = await _load(env, w.viewed)
    seen, stop = _capture(env.engine)
    try:
        async with env.sessions() as db:
            body = await tis.get_likely_affected_tests(db, run)
            assert not db.new and not db.dirty and not db.deleted
    finally:
        stop()
    # Bounded and read-only: range, readiness, candidates, prior ranges, co-failure, flaky -- SELECTs only.
    statements = [s for s in seen if s.lstrip().upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE"))]
    assert len(statements) == 6 and all(s.lstrip().upper().startswith("SELECT") for s in statements), statements
    assert (body["available"], body["reason"], body["source"], body["base_source"], body["base_anchor_is_strong"]) == (
        True, None, "supplied", "green_baseline", True)
    assert (body["changed_files_count"], body["file_detail_coverage"], body["total_candidates"]) == (3, 0.6667, 4)
    history = body["history"]
    assert (history["available"], history["insufficient_data_reason"], history["window_days"]) == (True, None, 90)
    assert (history["usable_runs"], history["runs_considered"], history["scan_capped"]) == (34, 32, False)
    rows = _by_key(body)
    assert list(rows) == ["charge", "refund", "cart"]           # 0.68, 0.6, 0.32; ui absent
    assert rows["charge"]["score"] == 0.68 and rows["charge"]["reason"] == {
        "overlapping_paths": ["payments/charge.py", "payments/refund.py"], "overlap_score": 1.0,
        "co_failure_runs": 1, "history_runs_considered": 8, "known_flaky": False}
    assert rows["refund"]["score"] == 0.6 and rows["refund"]["reason"]["co_failure_runs"] == 0
    assert rows["refund"]["reason"]["overlapping_paths"] == ["payments/refund.py"]
    # With P2 counted cart would be 14 (saturated, 0.4) and P2's flaky row would mark it known_flaky.
    assert rows["cart"]["score"] == 0.32 and rows["cart"]["reason"]["co_failure_runs"] == 4
    assert rows["cart"]["reason"]["known_flaky"] is False and rows["cart"]["reason"]["overlapping_paths"] == []
    assert (rows["charge"]["status_in_run"], rows["cart"]["status_in_run"]) == ("FAILED", "PASSED")


async def test_a_thin_project_gets_the_path_only_list(env):
    from app.services import test_impact_service as tis

    pid = await _project(env, "thin")
    viewed_at = env.now - timedelta(hours=1)
    for d in range(3):
        await _run(env, pid, viewed_at - timedelta(days=d + 1), [["payments/charge.py"]], {**ALL_PASS, "cart": "FAILED"})
    viewed = await _run(env, pid, viewed_at, VIEWED_RANGE, dict(ALL_PASS))
    async with env.sessions() as db:
        body = await tis.get_likely_affected_tests(db, await _load(env, viewed))
    history = body["history"]
    assert history["available"] is False and history["runs_considered"] == 0
    assert history["insufficient_data_reason"] == "only 4 of 4 runs carry a usable commit range (need 30)"
    assert body["available"] is True
    expected = sorted([FP["charge"], FP["refund"]])               # both 1.0: fingerprint order
    assert [t["test_fingerprint"] for t in body["tests"]] == expected
    for t in body["tests"]:
        assert t["score"] == 1.0
        assert t["reason"]["co_failure_runs"] is None and t["reason"]["history_runs_considered"] is None


async def _member_token(env, pid, tag):
    from app.models.postgres import ProjectMember, User, UserRole
    from app.services.auth_session_tokens import issue_access_jwt

    uid = uuid.uuid4()
    async with env.sessions() as db:
        db.add(User(id=uid, username=f"gb-{tag}-{uid.hex[:8]}", email=f"gb-{tag}-{uid.hex[:8]}@example.test",
                    hashed_password="!unusable", role=UserRole.VIEWER, is_active=True))
        await db.flush()
        if pid is not None:
            db.add(ProjectMember(project_id=pid, user_id=uid, role=UserRole.VIEWER))
        await db.commit()
        token = await issue_access_jwt(db, str(uid), timedelta(minutes=10))
    env.made.users.append(uid)
    return {"Authorization": f"Bearer {token}"}


async def test_the_route_end_to_end_is_read_only_and_guarded(env, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.core.config import settings
    from app.main import app
    from app.services import test_impact_service as tis

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    w = await _rich_world(env)
    bare = await _run(env, w.p1, env.now - timedelta(minutes=20), None, dict(ALL_PASS))  # no range row
    member = await _member_token(env, w.p1, "member")
    outsider = await _member_token(env, w.p2, "outsider")         # a member of the FOREIGN project only
    before = await _counts(env, [w.p1, w.p2])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gb-impact.test") as client:
        ok = await client.get(f"/api/v1/runs/{w.viewed}/likely-affected-tests", headers=member)
        one = await client.get(f"/api/v1/runs/{w.viewed}/likely-affected-tests", params={"limit": 1}, headers=member)
        gated = await client.get(f"/api/v1/runs/{bare}/likely-affected-tests", headers=member)
        refused = await client.get(f"/api/v1/runs/{w.viewed}/likely-affected-tests", headers=outsider)
        too_many = await client.get(f"/api/v1/runs/{w.viewed}/likely-affected-tests", params={"limit": 51},
                                    headers=member)
    assert ok.status_code == 200, ok.text
    async with env.sessions() as db:
        direct = await tis.get_likely_affected_tests(db, await _load(env, w.viewed))
    assert ok.json() == json.loads(json.dumps(direct))            # the route serves exactly the service's answer
    assert [t["test_fingerprint"] for t in one.json()["tests"]] == [FP["charge"]]
    assert gated.status_code == 200
    assert (gated.json()["available"], gated.json()["reason"], gated.json()["tests"]) == (False, "no_commit_range", [])
    assert gated.json()["total_candidates"] == 4
    assert (refused.status_code, refused.json()) == (404, {"detail": "Test run not found"})
    assert too_many.status_code == 422
    assert await _counts(env, [w.p1, w.p2]) == before             # nothing written by any of the reads
    async with env.sessions() as db:
        written = (await db.execute(text("SELECT count(*) FROM run_commit_ranges WHERE run_id = :r"),
                                    {"r": bare})).scalar_one()
    assert written == 0                                           # a GET never lazily resolves a range


async def test_timing_2000_tests_300_changed_files_30_prior_ranges(env):
    """B5: measured and printed (``-s``), not asserted -- the answer's shape is asserted.

    Every class carries the org token ``acme`` (a segment of every changed path; B7 drops it, all 2,000 tests
    carry it) and each test is its own class, so its class token is an atom of its own.
    """
    from app.models.postgres import RunCommitRange, TestRun
    from app.services import test_impact_service as tis

    pid = await _project(env, "timing")
    viewed_at = env.now - timedelta(hours=1)
    files = [f"src/main/java/com/acme/mod{k:02d}/Feature{j}.java" for k in range(30) for j in range(10)]
    run_ids: list[uuid.UUID] = []
    async with env.sessions() as db:
        for i in range(31):                                       # 30 prior runs, one a day, then the viewed run
            at = viewed_at - timedelta(days=30 - i) if i < 30 else viewed_at
            run = TestRun(id=uuid.uuid4(), project_id=pid, build_number=f"t-{i}", jenkins_job="gb", status="FAILED")
            run.created_at = at
            db.add(run)
            await db.flush()
            if i < 30:                                            # each prior range: 20 shared + 5 own paths
                commits = [{"sha": f"{i:040x}", "files": files[(i * 20) % 300:(i * 20) % 300 + 20]
                            + [f"legacy/old_{i}_{n}.py" for n in range(5)]}]
            else:                                                 # the viewed range: 300 files over 10 commits
                commits = [{"sha": f"{c:039x}f", "files": files[c * 30:(c + 1) * 30]} for c in range(10)]
            db.add(RunCommitRange(run_id=run.id, project_id=pid, source="supplied", base_source="supplied",
                                  commits=commits, resolved_at=at + timedelta(minutes=5)))
            run_ids.append(run.id)
        await db.flush()
        # 2,000 tests per run (62,000 rows); in prior run #ord the tests with g % 50 == ord % 50 fail.
        await db.execute(text(
            "INSERT INTO test_cases (id, test_run_id, test_name, class_name, test_fingerprint, status, suite_name) "
            "SELECT gen_random_uuid(), r.run_id, 'test_case_' || g, "
            "       'com.acme.mod' || (g % 100) || '.Feature' || g || 'Test', "
            "       md5('test_case_' || g || '\\:com.acme.mod' || (g % 100) || '.Feature' || g || 'Test'), "
            "       CASE WHEN r.ord <= 30 AND g % 50 = r.ord % 50 THEN 'FAILED' ELSE 'PASSED' END, 'timing' "
            "FROM unnest(CAST(:runs AS uuid[])) WITH ORDINALITY AS r(run_id, ord) "
            "CROSS JOIN generate_series(0, 1999) AS g"), {"runs": run_ids})
        await db.commit()
        await db.execute(text("ANALYZE test_cases"))
        await db.execute(text("ANALYZE run_commit_ranges"))
        await db.commit()
    run = await _load(env, run_ids[-1])
    async with env.sessions() as db:
        started = time.perf_counter()
        body = await tis.get_likely_affected_tests(db, run)
        elapsed = time.perf_counter() - started
    print(f"\nB5 likely-affected-tests: 2000 tests x 300 changed files, 30 prior ranges -> {elapsed:.3f}s")
    assert body["available"] is True and body["history"]["available"] is True
    assert (body["changed_files_count"], body["total_candidates"], body["history"]["runs_considered"]) == (300, 2000, 30)
    assert len(body["tests"]) == tis.DEFAULT_LIMIT
    assert all(t["reason"]["history_runs_considered"] == 30 for t in body["tests"])
    assert body["tests"] == sorted(body["tests"], key=lambda t: (-t["score"], t["test_fingerprint"]))
