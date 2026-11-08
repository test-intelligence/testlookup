"""W39 STRAT-07 — the observability evidence timeline against a real PostgreSQL AND a real MongoDB (contract §3).

What the statement-evaluating doubles in ``tests/test_run_observability_service.py`` cannot show:

1. **The real stores, the real queries.** A run whose newest ``decision_evidence_snapshots`` doc holds Prometheus
   ``app_metrics`` and a Splunk trace (and an OLDER snapshot that must not be shown), whose ``test_runs.ocp_metadata``
   holds the pod events stored at ingest, and whose two analysed test cases carry REAL tool observations
   (``query_splunk_logs`` / ``analyze_openshift_pod_events``, produced by the fetchers against the shared fake) plus
   ONE dev-seed-style row (``source: "splunk"``) that must never appear -- the ``CAST(json AS VARCHAR) LIKE`` filter,
   the join and the Mongo predicates run on real rows / documents.
2. **No leak.** A SECOND run of the same project, with its own snapshot, pod data and analysed test case, adds
   nothing to the first run's answer.
3. **Read-only, through the real app.** A VIEWER member's token gets exactly the service's answer; a member of
   another project gets the guard's 404; every SQL statement the build issues is a SELECT; the row counts of every
   table it reads and the Mongo documents (and the collection list) are unchanged.

Everything seeded lives under one fresh project (+ one foreign project and two users) and the runs' snapshot
documents; all of it is deleted at the end, in both stores. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (=
``DATABASE_URL``) at head and ``MONGO_URI`` / ``MONGO_DB``. To be listed in the protected Postgres step of
``.github/workflows/ci.yml`` (the step that runs the other ``*_postgres_mongo.py`` modules).
"""
from __future__ import annotations

import copy
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests import observability_timeline_samples as samples

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

UTC = timezone.utc
RUN_START = datetime(2026, 10, 20, 9, 50, tzinfo=UTC)
RUN_END = datetime(2026, 10, 20, 10, 5, tzinfo=UTC)
OTHER_RUN_MARK = "other-run-marker"        # appears only in the SECOND run's stored evidence


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
    from app.db.mongo import Collections, close_mongo, get_mongo_db

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    await close_mongo()                      # a Motor client binds to the loop it was built on
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    snapshots = get_mongo_db()[Collections.DECISION_EVIDENCE_SNAPSHOTS]
    made = SimpleNamespace(projects=[], users=[], runs=[])
    try:
        yield SimpleNamespace(engine=engine, sessions=sessions, made=made, snapshots=snapshots, mongo=get_mongo_db())
    finally:
        if made.runs:
            await snapshots.delete_many({"test_run_id": {"$in": [str(r) for r in made.runs]}})
        async with sessions() as db:   # cascades to runs, test cases, ai_analysis, members
            if made.projects:
                await db.execute(text("DELETE FROM projects WHERE id = ANY(:p)"), {"p": made.projects})
            if made.users:
                await db.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": made.users})
            await db.commit()
        await engine.dispose()
        if redis_client._pool is not None:   # only if the ASGI request path bound it
            await redis_client.close_redis()
        await close_mongo()
        await app_postgres.dispose_engine_for_loop()


async def _project(env, tag: str) -> uuid.UUID:
    pid = uuid.uuid4()
    async with env.sessions() as db:
        await db.execute(text("INSERT INTO projects (id, name, slug, is_active) VALUES (:i, :n, :n, true)"),
                         {"i": pid, "n": f"w39-obs-{tag}-{pid.hex[:6]}"})
        await db.commit()
    env.made.projects.append(pid)
    return pid


async def _run(env, pid, *, ocp_metadata, analyses):
    """One run (its own start / end), its pod data from ingest and its analysed test cases ``(name, refs)``."""
    from app.models.postgres import AIAnalysis, TestCase, TestRun

    async with env.sessions() as db:
        run = TestRun(id=uuid.uuid4(), project_id=pid, build_number=f"w39-{uuid.uuid4().hex[:8]}", jenkins_job="w39",
                      status="FAILED", start_time=RUN_START, end_time=RUN_END, ocp_metadata=ocp_metadata)
        db.add(run)
        await db.flush()
        cases = {}
        for name, refs in analyses:
            case = TestCase(id=uuid.uuid4(), test_run_id=run.id, test_name=name, class_name="w39.Obs",
                            test_fingerprint=uuid.uuid4().hex, status="FAILED", suite_name="obs")
            db.add(case)
            await db.flush()
            db.add(AIAnalysis(test_case_id=case.id, root_cause_summary="stored", evidence_references=refs,
                              tools_used=[ref.get("source") for ref in refs]))
            cases[name] = case.id
        await db.commit()
    env.made.runs.append(run.id)
    return SimpleNamespace(id=run.id, cases=cases)


async def _snapshot(env, pid, run_id, log_findings, *, created_at):
    doc = {
        "_id": uuid.uuid4().hex, "schema_version": 3, "project_id": str(pid), "test_run_id": str(run_id),
        "pipeline_run_id": str(uuid.uuid4()), "created_at": created_at, "content_sha256": "0" * 64,
        "canonical_decision": {"status": "complete", "log_findings": log_findings},
    }
    await env.snapshots.insert_one(copy.deepcopy(doc))
    return doc


def _findings(app_metrics, trace):
    cluster = {"cluster_id": "cluster_1", "status": "complete", "distributed_trace": trace, "log_anomaly": {},
               "log_summary": "", "evidence_refs": [], "app_metrics": app_metrics}
    return {**cluster, "cluster_findings": [dict(cluster)], "cluster_count": 1, "status": "complete"}


def _points():
    return samples.metric_points(start=datetime(2026, 10, 20, 9, 30, tzinfo=UTC).timestamp(), count=8, step=300.0)


SEED_REFS = [{"source": "splunk", "excerpt": "ERROR PaymentService: Connection timeout at 10:00:05"},   # seed_dev_data
             {"source": "ocp_events", "excerpt": "Pod restarted 3 times (OOMKilled)"}]


async def _world(env, monkeypatch):
    state = samples.wire_fake(monkeypatch)
    cfg = samples.config(state)
    trace = await samples.distributed_trace(cfg)
    splunk = samples.references(samples.SPLUNK_TOOL, await samples.splunk_text(cfg))
    ocp = samples.references(samples.OCP_TOOL, await samples.ocp_text(cfg))
    meta = await samples.ocp_metadata(cfg)
    # The second run's own evidence: a marked Splunk hit (its own index), a marked pod event, a marked snapshot.
    state.splunk_events["w39other"] = [{"_time": "2026-10-20T10:01:00.000+00:00", "level": "ERROR",
                                        "service": "checkout", "message": f"{OTHER_RUN_MARK} splunk hit",
                                        "host": "other-1"}]
    other_splunk = samples.references(samples.SPLUNK_TOOL,
                                      await samples.splunk_text(samples.config(state, index="w39other")))
    assert other_splunk[0]["excerpt"].startswith(samples.query_splunk.SPLUNK_FOUND_PREFIX)
    other_meta = copy.deepcopy(meta)
    other_meta["events"][0]["reason"] = OTHER_RUN_MARK
    p1, p2 = await _project(env, "p1"), await _project(env, "p2")
    viewed = await _run(env, p1, ocp_metadata=meta, analyses=[
        ("test_checkout_pays", splunk), ("test_runner_survives", ocp), ("test_seeded_by_dev_data", SEED_REFS)])
    other = await _run(env, p1, ocp_metadata=other_meta, analyses=[("test_other_run_only", other_splunk)])
    ok = samples.app_metrics("ok", "p95 latency for checkout rose 3.1× in the failure window",
                             [samples.p95_series(_points())])
    newest = await _snapshot(env, p1, viewed.id, _findings(ok, trace), created_at=datetime(2026, 10, 20, 10, 30))
    await _snapshot(env, p1, viewed.id, _findings(samples.app_metrics("no_data", "older analysis"), {}),
                    created_at=datetime(2026, 10, 20, 10, 10))
    marked = samples.app_metrics("no_data", OTHER_RUN_MARK)
    await _snapshot(env, p1, other.id, _findings(marked, {}), created_at=datetime(2026, 10, 20, 10, 40))
    await _snapshot(env, p2, viewed.id, _findings(marked, {}), created_at=datetime(2026, 10, 20, 11, 0))  # foreign
    return SimpleNamespace(p1=p1, p2=p2, viewed=viewed, other=other, newest=newest)


async def _counts(env, pids):
    queries = {
        "test_runs": "SELECT count(*) FROM test_runs WHERE project_id = ANY(:p)",
        "test_cases": "SELECT count(*) FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id "
                      "WHERE tr.project_id = ANY(:p)",
        "ai_analysis": "SELECT count(*) FROM ai_analysis a JOIN test_cases tc ON tc.id = a.test_case_id "
                       "JOIN test_runs tr ON tr.id = tc.test_run_id WHERE tr.project_id = ANY(:p)",
    }
    async with env.sessions() as db:
        counts = {name: (await db.execute(text(sql), {"p": pids})).scalar_one() for name, sql in queries.items()}
        counts["app_settings"] = (await db.execute(text("SELECT count(*) FROM app_settings"))).scalar_one()
        return counts


async def _mongo_state(env, run_ids):
    docs = await env.snapshots.find({"test_run_id": {"$in": [str(r) for r in run_ids]}}).sort("_id", 1).to_list(None)
    return docs, sorted(await env.mongo.list_collection_names())


def _capture(engine):
    seen: list[str] = []

    def before(_conn, _cursor, statement, _params, _context, _many):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", before)
    return seen, lambda: event.remove(engine.sync_engine, "before_cursor_execute", before)


async def _member_token(env, pid, tag):
    from app.models.postgres import ProjectMember, User, UserRole
    from app.services.auth_session_tokens import issue_access_jwt

    uid = uuid.uuid4()
    async with env.sessions() as db:
        db.add(User(id=uid, username=f"w39-{tag}-{uid.hex[:8]}", email=f"w39-{tag}-{uid.hex[:8]}@example.test",
                    hashed_password="!unusable", role=UserRole.VIEWER, is_active=True))
        await db.flush()
        db.add(ProjectMember(project_id=pid, user_id=uid, role=UserRole.VIEWER))
        await db.commit()
        token = await issue_access_jwt(db, str(uid), timedelta(minutes=10))
    env.made.users.append(uid)
    return {"Authorization": f"Bearer {token}"}


# ── The build on the real stores ─────────────────────────────────────────────────────────────────────────────────


async def test_the_build_reads_the_real_stores_shows_only_this_run_and_writes_nothing(env, monkeypatch):
    from app.services import run_observability_service as svc

    w = await _world(env, monkeypatch)
    before, mongo_before = await _counts(env, [w.p1, w.p2]), await _mongo_state(env, [w.viewed.id, w.other.id])
    seen, stop = _capture(env.engine)
    try:
        async with env.sessions() as db:
            body = await svc.build_run_observability_timeline(db, w.viewed.id)
            assert not db.new and not db.dirty and not db.deleted
    finally:
        stop()
    statements = [s for s in seen if s.lstrip().upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE", "WITH"))]
    assert len(statements) == 3 and all(s.lstrip().upper().startswith("SELECT") for s in statements), statements
    assert [lane["state"] for lane in body["lanes"]] == ["evidence"] * 5
    assert body["analysis"] == {"pipeline_run_id": w.newest["pipeline_run_id"],
                                "analysed_at": datetime(2026, 10, 20, 10, 30, tzinfo=UTC), "other_analyses": 1}
    prometheus, trace, ocp, splunk, checks = body["lanes"]
    assert [s["points"] for s in prometheus["series"]] == [_points()]
    assert [e["at"] for e in trace["events"]] == [datetime(2026, 10, 20, 10, 0, s, tzinfo=UTC) for s in (5, 7, 9)]
    assert [(e["title"], e["end"] is not None) for e in ocp["events"]] == [("OOMKilled", True), ("BackOff", False)]
    assert [(e["at"], e["test_case_id"]) for e in splunk["events"]] == [
        (datetime(2026, 10, 20, 10, 0, s, tzinfo=UTC), str(w.viewed.cases["test_checkout_pays"])) for s in (5, 7, 9)]
    assert [(u["test_name"], u["reason"]) for u in checks["untimed"]] == [("test_runner_survives", "no_time_stored")]
    assert body["axis"] == {"start": datetime(2026, 10, 20, 9, 30, tzinfo=UTC), "end": RUN_END}
    text_body = json.dumps(body, default=str)
    for leaked in (OTHER_RUN_MARK, "older analysis", "test_other_run_only", str(w.other.cases["test_other_run_only"]),
                   "Connection timeout at 10:00:05", "Pod restarted 3 times", samples.POD_HOST_IP, samples.POD_NODE):
        assert leaked not in text_body, leaked
    assert await _counts(env, [w.p1, w.p2]) == before
    assert await _mongo_state(env, [w.viewed.id, w.other.id]) == mongo_before


# ── Through the real app ─────────────────────────────────────────────────────────────────────────────────────────


async def test_the_route_serves_a_member_the_services_answer_and_refuses_an_outsider(env, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.models.schemas import RunObservabilityTimeline
    from app.services import run_observability_service as svc

    w = await _world(env, monkeypatch)
    member = await _member_token(env, w.p1, "member")
    outsider = await _member_token(env, w.p2, "outsider")       # a member of the FOREIGN project only
    before, mongo_before = await _counts(env, [w.p1, w.p2]), await _mongo_state(env, [w.viewed.id, w.other.id])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://w39-obs.test") as client:
        ok = await client.get(f"/api/v1/runs/{w.viewed.id}/observability-timeline", headers=member)
        other = await client.get(f"/api/v1/runs/{w.other.id}/observability-timeline", headers=member)
        refused = await client.get(f"/api/v1/runs/{w.viewed.id}/observability-timeline", headers=outsider)
    assert ok.status_code == 200, ok.text
    async with env.sessions() as db:
        direct = await svc.build_run_observability_timeline(db, w.viewed.id)
    assert ok.json() == RunObservabilityTimeline(**direct).model_dump(mode="json")
    assert other.status_code == 200 and other.json()["lanes"][0]["sentence"] == OTHER_RUN_MARK
    assert other.json()["lanes"][2]["events"][0]["title"] == OTHER_RUN_MARK
    assert (refused.status_code, refused.json()) == (404, {"detail": "Test run not found"})
    assert await _counts(env, [w.p1, w.p2]) == before
    assert await _mongo_state(env, [w.viewed.id, w.other.id]) == mongo_before
