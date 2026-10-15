"""W03 G-B on real PostgreSQL + MongoDB, through the real app: the AI-history routes.

The versions are written by the REAL producers (``stage_analysis_versions``,
``capture_analysis_digest``, ``record_run_summary_version``) over real rows;
the routes are called over HTTP with real JWTs. What only the assembled stack
can prove:

1. **The routes (R1-R7) and their goldens.** Two pipelines + a human correction
   of run "cur", one pipeline of its previous run "prev": the list (v2 llm, v1
   rules), one version, the v1 -> latest diff (verdict CONDITIONAL_GO -> NO_GO,
   INFRASTRUCTURE -> PRODUCT_BUG 40 -> 85, a new cluster), the per-test versions
   (v1 rules, v2 LLM, v3 human correction by Anna QA), the previous run, the
   cross-run compare (GO -> NO_GO, new + resolved root causes) and the export
   bundle -- each against ``tests/fixtures/ai_history/route_*.golden.json``
   after ids/instants/hashes are normalised (``UPDATE_AI_HISTORY_GOLDENS=1``
   rewrites them; read them before committing).
2. **The auth/IDOR matrix (§6.8).** A viewer gets 200s; a member of another
   project 404s; an admin on an unknown run 404s "Test run not found"; a
   version id of another run (same project or not) 404s "AI version not found"
   on R2 ``from``/``to``, R4 and R7 ``version_id``; a baseline of another
   project 404s "Baseline run not found"; a test of another run 404s.
3. **Mongo down** is 503 ``ai_history_unavailable`` on R1/R2(stored)/R4/R3 and a
   degraded 200 on the live compare. **No GET writes** (spied session + Mongo),
   except the export's E8.4 audit row; the export 409s when the gate refuses.
4. **D42 parity.** ``resolve_previous_run`` == ``resolve_suite_pair_for_run``
   for every completed run (branches, a tie broken by id, an in-progress
   candidate excluded) and an in-progress CURRENT run still gets its previous.
5. **The narrative** serves a ready ``run_compare_v2`` row ("AI narrative") and
   never a v1 row.
6. **Test history / canonical.** ``ai`` + ``ai_changes`` on the history points,
   ``version_count`` from real versions, nothing from the same fingerprint in
   another project; the canonical page's ``ai`` / ``ai_truncated``.

Every row is written under fresh projects and removed at the end (the database
is shared). Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head and a
reachable ``MONGO_URI``. Listed in the ``postgres-integration`` job of
``.github/workflows/ci.yml``.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

NOW = datetime.now(timezone.utc).replace(microsecond=0)
GOLDEN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "ai_history"
UPDATE_GOLDENS = os.environ.get("UPDATE_AI_HISTORY_GOLDENS") == "1"

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


def normalise(body):
    """Ids -> numbered uuids, 64-hex hashes -> numbered hex, instants -> one fixed instant, in order of
    appearance. The results stay VALID values, so the unit route test can feed a golden back through the
    response models."""
    ids: dict[str, str] = {}
    shas: dict[str, str] = {}

    def walk(value):
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, str):
            if _UUID.match(value):
                if value not in ids:
                    ids[value] = f"00000000-0000-4000-8000-{len(ids) + 1:012d}"
                return ids[value]
            if _SHA256.match(value):
                if value not in shas:
                    shas[value] = f"{len(shas) + 1:064x}"
                return shas[value]
            if _INSTANT.match(value):
                return "2026-01-01T00:00:00Z"
        return value

    return walk(body)


def check_golden(name: str, body) -> None:
    path = GOLDEN_DIR / f"route_{name}.golden.json"
    got = normalise(body)
    if UPDATE_GOLDENS or not path.exists():
        if not UPDATE_GOLDENS:
            pytest.fail(f"{path.name} is missing: run with UPDATE_AI_HISTORY_GOLDENS=1, then READ it")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(got, indent=2, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
        return
    assert got == json.loads(path.read_text(encoding="utf-8")), f"{path.name} drifted"


# ── The world ────────────────────────────────────────────────────────────────


def _run(project_id, build, *, minutes_ago, branch="main", suite="checkout", status="FAILED", duration=10,
         finished=True):
    from app.models.postgres import TestRun

    created = NOW - timedelta(minutes=minutes_ago)
    row = TestRun(
        id=uuid.uuid4(), project_id=project_id, build_number=build, jenkins_job="aih", status=status,
        ingestion_source="upload", branch=branch, primary_suite_name=suite, total_tests=2, failed_tests=2,
        end_time=created + timedelta(minutes=duration) if finished else None,
    )
    row.created_at = created
    return row


def _case(run, name, fp):
    from app.models.postgres import TestCase

    return TestCase(id=uuid.uuid4(), test_run_id=run.id, test_name=name, class_name="checkout",
                    suite_name="checkout", test_fingerprint=fp, status="FAILED", duration_ms=1000,
                    error_message=f"{name} failed")


def _analysis(case, category, confidence, root_cause, mode):
    from app.models.postgres import AIAnalysis

    llm = mode == "llm"
    return AIAnalysis(
        test_case_id=case.id, failure_category=category, confidence_score=confidence,
        root_cause_summary=root_cause, is_flaky=False, requires_human_review=False,
        llm_provider="ollama" if llm else None, llm_model="llama3.1" if llm else None,
        routing_metadata={"analysis_mode": mode},
    )


def _set(row, category, confidence, root_cause, mode):
    llm = mode == "llm"
    row.failure_category, row.confidence_score, row.root_cause_summary = category, confidence, root_cause
    row.llm_provider, row.llm_model = ("ollama", "llama3.1") if llm else (None, None)
    row.routing_metadata = {"analysis_mode": mode}


def _cluster(run, pipeline, label, members):
    from app.models.postgres import FailureCluster

    return FailureCluster(test_run_id=run.id, pipeline_run_id=pipeline, cluster_id="cl_001", label=label,
                          representative_error=label, member_test_ids=[str(m.id) for m in members],
                          size=len(members))


async def _fresh_analysis(db, case):
    """The row as stored now (a detached copy would carry an older state into a merge)."""
    from app.models.postgres import AIAnalysis

    return (await db.execute(select(AIAnalysis).where(AIAnalysis.test_case_id == case.id))).scalar_one()


async def _version(db, mongo, run, pipeline, analyses):
    """What the pipeline does: stage the per-test versions, then capture the digest."""
    from app.services import ai_history_service as ahs

    await db.flush()
    await ahs.stage_analysis_versions(
        db, [ahs.snapshot_from_orm(a) for a in analyses], trigger="pipeline", pipeline_run_id=pipeline, mongo=mongo,
    )
    result = await ahs.capture_analysis_digest(db, mongo, run_id=run.id, pipeline_run_id=pipeline)
    assert result.version is not None and result.version.created, result
    await db.commit()
    return result.version


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.db import mongo as app_mongo
    from app.db import postgres as app_postgres
    from app.db.mongo import Collections, ensure_indexes, get_mongo_db
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import (
        AgentPipelineRun,
        CanonicalTestCase,
        Project,
        ProjectMember,
        ReleaseDecision,
        TestCaseHistory,
        TestSuite,
        User,
        UserRole,
    )
    from app.services import ai_history_service as ahs

    dsn = _env("TESTLOOKUP_POSTGRES_TEST_DSN")
    await app_postgres.dispose_engine_for_loop()
    app_mongo.reset_mongo_client()  # bind Motor to THIS module's loop
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    spy = SimpleNamespace(adds=0, flushes=0, commits=0)

    async def _get_db():
        async with sessions() as session:
            add, flush, commit = session.add, session.flush, session.commit

            def counted_add(*a, **k):
                spy.adds += 1
                return add(*a, **k)

            async def counted_flush(*a, **k):
                spy.flushes += 1
                return await flush(*a, **k)

            async def counted_commit(*a, **k):
                spy.commits += 1
                return await commit(*a, **k)

            session.add, session.flush, session.commit = counted_add, counted_flush, counted_commit
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    patch = pytest.MonkeyPatch()
    app.dependency_overrides[get_db] = _get_db
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)
    mongo = get_mongo_db()
    await ensure_indexes()

    tag = uuid.uuid4().hex[:10]
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    admin, viewer, anna, outsider = (uuid.uuid4() for _ in range(4))
    w = SimpleNamespace(tag=tag, p1=p1, p2=p2, spy=spy, mongo=mongo, sessions=sessions)
    try:
        async with sessions() as db:
            db.add(Project(id=p1, name=f"aih-p1-{tag}", slug=f"aih-p1-{tag}", is_active=True))
            db.add(Project(id=p2, name=f"aih-p2-{tag}", slug=f"aih-p2-{tag}", is_active=True))
            for uid, label, role, full in (
                (admin, "admin", UserRole.ADMIN, "Admin"),
                (viewer, "viewer", UserRole.VIEWER, "Vera Viewer"),
                (anna, "anna", UserRole.QA_ENGINEER, "Anna QA"),
                (outsider, "outsider", UserRole.QA_ENGINEER, "Otto Outsider"),
            ):
                db.add(User(id=uid, email=f"aih-{label}-{tag}@example.com", username=f"aih_{label}_{tag}",
                            full_name=full, hashed_password="!unusable", role=role.value))
            await db.flush()
            db.add(ProjectMember(project_id=p1, user_id=viewer, role=UserRole.VIEWER.value))
            db.add(ProjectMember(project_id=p1, user_id=anna, role=UserRole.QA_ENGINEER.value))
            db.add(ProjectMember(project_id=p2, user_id=outsider, role=UserRole.QA_ENGINEER.value))

            # Runs of suite "checkout" in p1. main: prev (-3h) < in-progress (-2.5h) < cur (-2h) < later (-1h).
            w.prev = _run(p1, "100", minutes_ago=180)
            w.inprog = _run(p1, "104", minutes_ago=150, status="IN_PROGRESS", finished=False)
            w.cur = _run(p1, "101", minutes_ago=120)
            w.later = _run(p1, "102", minutes_ago=60, status="PASSED")
            w.dev_old = _run(p1, "95", minutes_ago=300, branch="dev", status="PASSED")
            w.dev_run = _run(p1, "99", minutes_ago=150, branch="dev")
            w.tie_a = _run(p1, "90", minutes_ago=240, branch="tie")
            w.tie_b = _run(p1, "91", minutes_ago=240, branch="tie")
            w.tie_cur = _run(p1, "92", minutes_ago=210, branch="tie")
            w.nosuite = _run(p1, "103", minutes_ago=100, suite=None)
            w.export_run = _run(p1, 'rel"1\r\n/2', minutes_ago=90, branch="exp")
            w.foreign = _run(p2, "100", minutes_ago=120)
            db.add_all([w.prev, w.inprog, w.cur, w.later, w.dev_old, w.dev_run, w.tie_a, w.tie_b, w.tie_cur,
                        w.nosuite, w.export_run, w.foreign])
            await db.flush()
            w.prev_pay, w.prev_db = _case(w.prev, "test_pay", "fp-pay"), _case(w.prev, "test_db", "fp-db")
            w.cur_pay, w.cur_cart = _case(w.cur, "test_pay", "fp-pay"), _case(w.cur, "test_cart", "fp-cart")
            w.f_pay = _case(w.foreign, "test_pay", "fp-pay")
            db.add_all([w.prev_pay, w.prev_db, w.cur_pay, w.cur_cart, w.f_pay])
            await db.flush()
            for case, run in ((w.prev_pay, w.prev), (w.cur_pay, w.cur), (w.f_pay, w.foreign)):
                db.add(TestCaseHistory(test_case_id=case.id, test_run_id=run.id, test_fingerprint=case.test_fingerprint,
                                       status="FAILED", duration_ms=1000, created_at=run.created_at))
            suite = TestSuite(id=uuid.uuid4(), project_id=p1, name=f"checkout-{tag}")
            db.add(suite)
            await db.flush()
            w.canonical = CanonicalTestCase(id=uuid.uuid4(), project_id=p1, test_suite_id=suite.id,
                                            test_fingerprint="fp-pay", test_name="test_pay")
            db.add(w.canonical)
            await db.flush()
            w.prev_pay.canonical_test_case_id = w.canonical.id
            w.cur_pay.canonical_test_case_id = w.canonical.id
            w.pipe0, w.pipe1, w.pipe2, w.pipe_f = (uuid.uuid4() for _ in range(4))
            for pid, run in ((w.pipe0, w.prev), (w.pipe1, w.cur), (w.pipe2, w.cur), (w.pipe_f, w.foreign)):
                db.add(AgentPipelineRun(id=pid, test_run_id=run.id, workflow_type="offline", status="completed",
                                        completed_at=NOW))
            await db.commit()

        # prev: one rules pipeline; verdict GO.
        async with sessions() as db:
            rows = [_analysis(w.prev_pay, "INFRASTRUCTURE", 40, "DB connection refused", "rules"),
                    _analysis(w.prev_db, "INFRASTRUCTURE", 45, "DB connection refused", "rules")]
            db.add_all(rows)
            db.add(_cluster(w.prev, w.pipe0, "DB connection refused", [w.prev_db]))
            db.add(ReleaseDecision(test_run_id=w.prev.id, pipeline_run_id=w.pipe0, recommendation="GO", risk_score=35))
            w.prev_v1 = await _version(db, mongo, w.prev, w.pipe0, rows)

        # cur v1: rules, CONDITIONAL_GO.
        async with sessions() as db:
            w.cur_pay_ai = _analysis(w.cur_pay, "INFRASTRUCTURE", 40, "Gateway returned 502", "rules")
            w.cur_cart_ai = _analysis(w.cur_cart, "INFRASTRUCTURE", 45, "DB connection refused", "rules")
            db.add_all([w.cur_pay_ai, w.cur_cart_ai])
            db.add(_cluster(w.cur, w.pipe1, "DB connection refused", [w.cur_cart]))
            w.cur_rd = ReleaseDecision(test_run_id=w.cur.id, pipeline_run_id=w.pipe1, recommendation="CONDITIONAL_GO",
                                       risk_score=55)
            db.add(w.cur_rd)
            w.cur_v1 = await _version(db, mongo, w.cur, w.pipe1, [w.cur_pay_ai, w.cur_cart_ai])

        # cur v2: LLM re-analysis, NO_GO, a new root cause. The summary stage stores its run summary
        # BEFORE the pipeline ends (and the hook captures the digest), as in the real pipeline.
        summary = ahs.run_summary_payload({
            "executive_summary": "Two checkout tests fail on a payment-gateway timeout.",
            "markdown_report": "## Summary", "layer1_executive_summary": "Gateway timeout.",
            "analysis_count": 2, "fallback_used": False,
            "summary_provenance": {"model_config_snapshot": {"provider": "ollama", "model": "llama3.1"}},
        })
        w.cur_summary = await ahs.record_run_summary_version(
            mongo, test_run_id=str(w.cur.id), project_id=str(p1), pipeline_run_id=str(w.pipe2), payload=summary,
        )
        assert w.cur_summary is not None
        async with sessions() as db:
            pay, cart = await _fresh_analysis(db, w.cur_pay), await _fresh_analysis(db, w.cur_cart)
            _set(pay, "PRODUCT_BUG", 85, "Timeout waiting for payment-gateway", "llm")
            _set(cart, "PRODUCT_BUG", 80, "Timeout waiting for payment-gateway", "llm")
            db.add(_cluster(w.cur, w.pipe2, "Timeout waiting for payment-gateway", [w.cur_pay]))
            rd = (await db.execute(select(ReleaseDecision).where(ReleaseDecision.test_run_id == w.cur.id))).scalar_one()
            rd.pipeline_run_id, rd.recommendation, rd.risk_score = w.pipe2, "NO_GO", 72
            w.cur_v2 = await _version(db, mongo, w.cur, w.pipe2, [pay, cart])

        # A human correction of cur's test_pay by Anna (live only: no digest version).
        async with sessions() as db:
            pay = await _fresh_analysis(db, w.cur_pay)
            pay.failure_category = "AUTOMATION_DEFECT"
            await db.flush()
            await ahs.stage_analysis_version(db, ahs.snapshot_from_orm(pay), trigger="human_correction",
                                             actor_user_id=anna, mongo=mongo)
            await db.commit()

        # The foreign project's run: same suite, same fingerprint.
        async with sessions() as db:
            row = _analysis(w.f_pay, "PRODUCT_BUG", 90, "Foreign secret root cause", "llm")
            db.add(row)
            db.add(ReleaseDecision(test_run_id=w.foreign.id, pipeline_run_id=w.pipe_f, recommendation="NO_GO",
                                   risk_score=90))
            w.foreign_v1 = await _version(db, mongo, w.foreign, w.pipe_f, [row])

        def _jwt(uid):
            return {"Authorization": f"Bearer {create_access_token(str(uid))}"}

        w.admin, w.viewer, w.anna, w.outsider = _jwt(admin), _jwt(viewer), _jwt(anna), _jwt(outsider)
        w.admin_id = admin
        w.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            yield w
        finally:
            await w.client.aclose()
    finally:
        app.dependency_overrides.pop(get_db, None)
        try:
            await mongo[Collections.AI_RUN_VERSIONS].delete_many({"project_id": {"$in": [str(p1), str(p2)]}})
        except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
            print(f"aih teardown (mongo): {type(exc).__name__}")
        for statement in (
            text("DELETE FROM projects WHERE id IN (:a, :b)"),
            text("DELETE FROM access_audit_logs WHERE actor_user_id IN (:u1, :u2, :u3, :u4)"),
            delete(User).where(User.email.like(f"aih-%-{tag}@example.com")),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement, {"a": p1, "b": p2, "u1": admin, "u2": viewer, "u3": anna,
                                                 "u4": outsider})
            except Exception as exc:  # noqa: BLE001
                print(f"aih teardown: {type(exc).__name__}: {str(exc)[:160]}")
        patch.undo()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()
        app_mongo.reset_mongo_client()


async def _get(world, path, headers, **params):
    return await world.client.get(path, params=params, headers=headers)


def _routes(w):
    """Every route of the family for run cur, with valid params."""
    base = f"/api/v1/runs/{w.cur.id}"
    return [
        (f"{base}/ai-history", {}),
        (f"{base}/ai-history/diff", {"from": w.cur_v1.version_id}),
        (f"{base}/ai-history/{w.cur_v1.version_id}", {}),
        (f"{base}/tests/{w.cur_pay.id}/ai-history", {}),
        (f"{base}/previous", {}),
        (f"{base}/ai-compare", {}),
    ]


# ── 1. Routes + goldens ──────────────────────────────────────────────────────


async def test_r1_lists_the_two_versions_newest_first(world):
    resp = await _get(world, f"/api/v1/runs/{world.cur.id}/ai-history", world.viewer, limit=100)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [(v["version"], v["analysis_mode"], v["verdict"]) for v in body["versions"]] == [
        (2, "llm", "NO_GO"), (1, "rules", "CONDITIONAL_GO"),
    ]
    assert body["versions"][0]["llm_model"] == "llama3.1" and body["versions"][0]["is_latest"] is True
    assert body["versions"][0]["summary_version"] == 1, "the paired run summary of the same pipeline"
    assert body["live"] == {"available": True} and body["latest_version"] == 2 and body["total"] == 2
    assert body["versions"][1]["review"]["state"] == "pending_review"
    assert resp.headers["X-TestLookup-AI-Generated"] == "true"
    check_golden("r1_list", body)

    summaries = (await _get(world, f"/api/v1/runs/{world.cur.id}/ai-history", world.viewer,
                            kind="run_summary")).json()
    assert [(v["version"], v["kind"], v["analysis_mode"]) for v in summaries["versions"]] == [
        (1, "run_summary", "llm"),
    ]
    assert summaries["versions"][0]["headline"] == "Two checkout tests fail on a payment-gateway timeout."
    assert (await _get(world, f"/api/v1/runs/{world.cur.id}/ai-history", world.viewer,
                       kind="digest")).status_code == 422


async def test_r4_returns_one_version_with_its_payload(world):
    resp = await _get(world, f"/api/v1/runs/{world.cur.id}/ai-history/{world.cur_v1.version_id}", world.viewer)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 1 and body["is_latest"] is False and body["payload"]["kind"] == "analysis_digest"
    assert body["payload"]["provenance"]["engine"] == "rules" and body["requires_human_review"] is True
    check_golden("r4_version", body)


async def test_r2_diffs_v1_against_latest_and_against_live(world):
    resp = await _get(world, f"/api/v1/runs/{world.cur.id}/ai-history/diff", world.viewer,
                      **{"from": world.cur_v1.version_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    diff = body["diff"]
    assert body["from"]["version"] == 1 and body["to"]["version"] == 2 and body["to"]["source"] == "stored"
    assert diff["verdict"]["from"] == "CONDITIONAL_GO" and diff["verdict"]["to"] == "NO_GO"
    assert diff["verdict"]["direction"] == "worse"
    pay = next(t for t in diff["tests"]["changed"] if t["test_name"] == "test_pay")
    assert pay["category"] == {"from": "INFRASTRUCTURE", "to": "PRODUCT_BUG"}
    assert pay["confidence"] == {"from": 40, "to": 85, "delta": 45}
    assert [c["label"] for c in diff["clusters"]["added"]] == ["Timeout waiting for payment-gateway"]
    assert [c["label"] for c in diff["clusters"]["removed"]] == ["DB connection refused"]
    assert diff["provenance"]["engine"] == {"from": "rules", "to": "llm"}
    check_golden("r2_diff", body)

    live = (await _get(world, f"/api/v1/runs/{world.cur.id}/ai-history/diff", world.viewer,
                       **{"from": world.cur_v2.version_id, "to": "live"})).json()
    assert live["to"]["source"] == "live"
    pay = next(t for t in live["diff"]["tests"]["changed"] if t["test_name"] == "test_pay")
    assert pay["category"] == {"from": "PRODUCT_BUG", "to": "AUTOMATION_DEFECT"}, "the human correction"


async def test_r5_lists_the_per_test_versions_with_the_human(world):
    resp = await _get(world, f"/api/v1/runs/{world.cur.id}/tests/{world.cur_pay.id}/ai-history", world.viewer)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [(v["version"], v["trigger"], v["analysis_mode"]) for v in body["versions"]] == [
        (3, "human_correction", "llm"), (2, "pipeline", "llm"), (1, "pipeline", "rules"),
    ]
    assert body["versions"][0]["actor"]["name"] == "Anna QA"
    assert body["current"]["trigger"] == "current" and body["current"]["version"] == 3
    assert body["current"]["failure_category"] == "AUTOMATION_DEFECT" and body["total"] == 3
    assert "evidence_references" not in json.dumps(body)
    check_golden("r5_test_versions", body)


async def test_r6_finds_the_previous_run_of_the_suite_on_the_branch(world):
    resp = await _get(world, f"/api/v1/runs/{world.cur.id}/previous", world.viewer)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["baseline"]["run_id"] == str(world.prev.id), "not dev's run, not the in-progress one"
    assert body["reason"] is None and body["rule"]["branch"] == "main"
    check_golden("r6_previous", body)

    none = (await _get(world, f"/api/v1/runs/{world.nosuite.id}/previous", world.viewer)).json()
    assert none["reason"] == "no_primary_suite" and none["baseline"] is None
    first = (await _get(world, f"/api/v1/runs/{world.prev.id}/previous", world.viewer)).json()
    assert first["reason"] == "no_previous_run"
    assert first["message"] == 'No earlier completed run of "checkout" on branch "main" found.'
    lacks = (await _get(world, f"/api/v1/runs/{world.cur.id}/previous", world.viewer, suite_name="payments")).json()
    assert lacks["reason"] == "run_lacks_suite"


async def test_r7_compares_with_the_previous_run(world):
    resp = await _get(world, f"/api/v1/runs/{world.cur.id}/ai-compare", world.viewer)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["baseline"]["run_id"] == str(world.prev.id) and body["baseline"]["selection"] == "default"
    diff = body["diff"]
    assert (diff["verdict"]["from"], diff["verdict"]["to"], diff["verdict"]["direction"]) == ("GO", "NO_GO", "worse")
    assert [c["label"] for c in diff["clusters"]["added"]] == ["Timeout waiting for payment-gateway"]
    assert [c["label"] for c in diff["clusters"]["removed"]] == ["DB connection refused"]
    assert [t["test_name"] for t in diff["tests"]["added"]] == ["test_cart"]
    assert [t["test_name"] for t in diff["tests"]["removed"]] == ["test_db"]
    assert body["narrative"]["label"] == "deterministic summary" and body["narrative"]["report_status"] == "absent"
    assert body["sides"]["baseline"]["verdict"] == "GO" and body["sides"]["current"]["verdict"] == "NO_GO"
    assert "Foreign secret" not in resp.text
    check_golden("r7_compare", body)

    pinned = (await _get(world, f"/api/v1/runs/{world.cur.id}/ai-compare", world.viewer,
                         baseline_version_id=world.prev_v1.version_id, version_id=world.cur_v1.version_id)).json()
    assert pinned["baseline_ref"]["source"] == "stored" and pinned["current_ref"]["version"] == 1
    assert pinned["diff"]["verdict"]["to"] == "CONDITIONAL_GO"


async def test_r3_exports_the_whole_history_and_audits_the_distribution(world):
    from app.models.postgres import AccessAuditLog

    resp = await _get(world, f"/api/v1/runs/{world.cur.id}/ai-history/export", world.viewer)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-disposition"] == 'attachment; filename="run-101-ai-history.json"'
    body = json.loads(resp.text)
    assert [v["version"] for v in body["versions"]] == [1, 2]
    assert [(d["from_version"], d["to_version"]) for d in body["diffs"]] == [(1, 2)]
    assert body["per_test_versions"]["total"] == 5 and body["review"]["state"] == "pending_review"
    check_golden("r3_export", body)
    async with world.sessions() as db:
        actions = (await db.execute(
            select(AccessAuditLog.action, AccessAuditLog.after_value).where(AccessAuditLog.project_id == world.p1)
        )).all()
    assert any(a == "ai_report.distribution_would_refuse" and v["channel"] == "ai_history_export"
               for a, v in actions), actions


# ── 2. Auth / IDOR (§6.8) ────────────────────────────────────────────────────


async def test_a_viewer_gets_200_on_every_route_and_an_outsider_404(world):
    for path, params in _routes(world):
        assert (await _get(world, path, world.viewer, **params)).status_code == 200, path
        resp = await _get(world, path, world.outsider, **params)
        assert resp.status_code == 404 and resp.json() == {"detail": "Test run not found"}, path
    assert (await _get(world, f"/api/v1/runs/{world.cur.id}/ai-history/export", world.outsider)).status_code == 404


async def test_an_admin_on_an_unknown_run_gets_404_not_a_crash(world):
    missing = uuid.uuid4()
    for path, params in _routes(world):
        resp = await _get(world, path.replace(str(world.cur.id), str(missing)), world.admin, **params)
        assert resp.status_code == 404 and resp.json() == {"detail": "Test run not found"}, path
    resp = await _get(world, f"/api/v1/runs/{missing}/ai-history/export", world.admin)
    assert resp.status_code == 404


async def test_a_version_id_of_another_run_is_404_everywhere(world):
    base = f"/api/v1/runs/{world.cur.id}"
    for other in (world.prev_v1.version_id, world.foreign_v1.version_id):  # same project, another project
        for path, params in (
            (f"{base}/ai-history/{other}", {}),
            (f"{base}/ai-history/diff", {"from": other}),
            (f"{base}/ai-history/diff", {"from": world.cur_v1.version_id, "to": other}),
            (f"{base}/ai-compare", {"version_id": other}),
        ):
            resp = await _get(world, path, world.viewer, **params)
            assert resp.status_code == 404 and resp.json() == {"detail": "AI version not found"}, (path, params)
            assert "Foreign secret" not in resp.text
    resp = await _get(world, f"{base}/ai-compare", world.viewer, baseline_run_id=str(world.prev.id),
                      baseline_version_id=world.foreign_v1.version_id)
    assert resp.status_code == 404 and resp.json() == {"detail": "AI version not found"}
    assert (await _get(world, f"{base}/ai-history/not-a-uuid", world.viewer)).status_code == 422
    assert (await _get(world, f"{base}/ai-history/diff", world.viewer, **{"from": "v1"})).status_code == 422
    assert (await _get(world, f"{base}/ai-history/diff", world.viewer,
                       **{"from": world.cur_v1.version_id, "to": "v2"})).status_code == 422


async def test_a_baseline_of_another_project_is_404_and_the_run_itself_422(world):
    base = f"/api/v1/runs/{world.cur.id}/ai-compare"
    resp = await _get(world, base, world.viewer, baseline_run_id=str(world.foreign.id))
    assert resp.status_code == 404 and resp.json() == {"detail": "Baseline run not found"}
    resp = await _get(world, base, world.admin, baseline_run_id=str(uuid.uuid4()))
    assert resp.status_code == 404 and resp.json() == {"detail": "Baseline run not found"}
    assert (await _get(world, base, world.viewer, baseline_run_id=str(world.cur.id))).status_code == 422
    explicit = (await _get(world, base, world.viewer, baseline_run_id=str(world.dev_run.id))).json()
    assert explicit["baseline"]["selection"] == "explicit" and explicit["baseline"]["branch_mismatch"] is True


async def test_a_test_of_another_run_is_404(world):
    resp = await _get(world, f"/api/v1/runs/{world.cur.id}/tests/{world.prev_pay.id}/ai-history", world.viewer)
    assert resp.status_code == 404 and resp.json() == {"detail": "Test case not found"}
    resp = await _get(world, f"/api/v1/runs/{world.cur.id}/tests/{world.f_pay.id}/ai-history", world.admin)
    assert resp.status_code == 404


# ── 3. Mongo down, no writes, the refused export ─────────────────────────────


async def test_mongo_down_is_503_on_stored_reads_and_degrades_the_live_compare(world, monkeypatch):
    from tests.fakes.fake_ai_history_store import FakeMongo

    down = FakeMongo()
    down.fail = True
    monkeypatch.setattr("app.routers.ai_history.get_mongo_db", lambda: down)
    base = f"/api/v1/runs/{world.cur.id}"
    for path, params in (
        (f"{base}/ai-history", {}),
        (f"{base}/ai-history/{world.cur_v1.version_id}", {}),
        (f"{base}/ai-history/diff", {"from": world.cur_v1.version_id}),
        (f"{base}/ai-history/export", {}),
        (f"{base}/ai-compare", {"version_id": world.cur_v1.version_id}),
    ):
        resp = await _get(world, path, world.viewer, **params)
        assert resp.status_code == 503 and resp.json() == {"detail": "ai_history_unavailable"}, path
    live = await _get(world, f"{base}/ai-compare", world.viewer)
    assert live.status_code == 200, live.text
    assert live.json()["diff"]["verdict"]["to"] == "NO_GO", "the release decision still answers"
    assert (await _get(world, f"{base}/tests/{world.cur_pay.id}/ai-history", world.viewer)).status_code == 200
    assert (await _get(world, f"{base}/previous", world.viewer)).status_code == 200


async def test_no_get_writes_anything(world, monkeypatch):
    real = world.mongo
    writes: list[str] = []

    class _Coll:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            if name.startswith(("insert", "update", "delete", "replace", "find_one_and", "bulk")):
                writes.append(name)
            return getattr(self._inner, name)

    class _Db:
        def __getitem__(self, name):
            return _Coll(real[name])

    monkeypatch.setattr("app.routers.ai_history.get_mongo_db", lambda: _Db())
    world.spy.adds = world.spy.flushes = world.spy.commits = 0
    for path, params in _routes(world) + [
        (f"/api/v1/runs/{world.cur.id}/ai-history/diff", {"from": world.cur_v1.version_id, "to": "live"}),
    ]:
        assert (await _get(world, path, world.viewer, **params)).status_code == 200, path
    assert (world.spy.adds, world.spy.flushes, world.spy.commits) == (0, 0, 0)
    assert writes == []
    # The one documented write: the export's E8.4 audit row, committed.
    assert (await _get(world, f"/api/v1/runs/{world.cur.id}/ai-history/export", world.viewer)).status_code == 200
    assert world.spy.adds == 1 and world.spy.commits == 1 and writes == []


async def test_an_enforced_review_gate_refuses_the_export_with_an_audit_row(world, monkeypatch):
    from app.core.config import settings
    from app.models.postgres import AccessAuditLog

    monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", True)
    resp = await _get(world, f"/api/v1/runs/{world.cur.id}/ai-history/export", world.viewer)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "report_pending_review"
    async with world.sessions() as db:
        refused = (await db.execute(
            select(func.count()).select_from(AccessAuditLog).where(
                AccessAuditLog.project_id == world.p1, AccessAuditLog.action == "ai_report.distribution_refused",
            )
        )).scalar_one()
    assert refused == 1


async def test_the_export_filename_cannot_inject_a_header(world):
    resp = await _get(world, f"/api/v1/runs/{world.export_run.id}/ai-history/export", world.viewer)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-disposition"] == 'attachment; filename="run-rel_1___2-ai-history.json"'
    assert json.loads(resp.text)["versions"] == []


# ── 4. D42 parity with resolve_suite_pair_for_run ────────────────────────────


async def test_the_previous_run_rule_is_the_suite_pair_rule(world):
    from app.services.ai_history_read_service import resolve_previous_run
    from app.services.run_compare_service import resolve_suite_pair_for_run

    expected = {
        world.cur.id: world.prev.id,
        world.later.id: world.cur.id,
        world.prev.id: None,
        world.dev_run.id: world.dev_old.id,
        world.dev_old.id: None,
        world.tie_cur.id: min(world.tie_a.id, world.tie_b.id),
        world.export_run.id: None,
    }
    async with world.sessions() as db:
        for run_id, want in expected.items():
            run = await db.get(type(world.cur), run_id)
            ours = await resolve_previous_run(db, run=run)
            try:
                theirs, _ = await resolve_suite_pair_for_run(
                    db, project_id=world.p1, suite_name="checkout", right_run_id=run_id,
                )
                theirs_id = theirs.id
            except LookupError:
                theirs_id = None
            got = ours.baseline.id if ours.baseline else None
            assert got == theirs_id == want, (run.build_number, got, theirs_id, want)
        # An in-progress CURRENT run still has a previous run (the pair rule refuses it).
        inprog = await db.get(type(world.cur), world.inprog.id)
        assert (await resolve_previous_run(db, run=inprog)).baseline.id == world.prev.id


# ── 5. The narrative ─────────────────────────────────────────────────────────


async def test_a_v1_report_is_never_served_as_the_v2_narrative(world):
    from app.models.postgres import RunComparisonReport

    path = f"/api/v1/runs/{world.cur.id}/ai-compare"
    common = dict(project_id=world.p1, left_run_id=world.prev.id, right_run_id=world.cur.id, suite_name="checkout",
                  suite_name_normalized="checkout", compare_payload={}, status="ready", model_name="llama3.1")
    async with world.sessions() as db:
        db.add(RunComparisonReport(prompt_version="run_compare_v1", fallback_used=False,
                                   ai_report={"executive_summary": "OLD v1 narrative", "fallback_used": False},
                                   **common))
        await db.commit()
    body = (await _get(world, path, world.viewer)).json()
    assert body["narrative"]["label"] == "deterministic summary" and "OLD v1" not in json.dumps(body)

    async with world.sessions() as db:
        db.add(RunComparisonReport(prompt_version="run_compare_v2", fallback_used=False,
                                   ai_report={"executive_summary": "The payment gateway regressed.",
                                              "fallback_used": False, "risk_level": "HIGH",
                                              "ai_changes": ["Verdict GO -> NO_GO (risk 35 -> 72)"]},
                                   **common))
        await db.commit()
    narrative = (await _get(world, path, world.viewer)).json()["narrative"]
    assert (narrative["label"], narrative["source"], narrative["status"]) == ("AI narrative", "llm", "ready")
    assert narrative["executive_summary"] == "The payment gateway regressed."
    async with world.sessions() as db:
        await db.execute(delete(RunComparisonReport).where(RunComparisonReport.project_id == world.p1))
        await db.commit()


# ── 6. Test history and the canonical page ───────────────────────────────────


async def test_the_test_history_carries_ai_and_where_it_changed(world):
    resp = await _get(world, f"/api/v1/runs/{world.cur.id}/tests/{world.cur_pay.id}/history", world.viewer)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "Foreign secret" not in resp.text, "a same-fingerprint test of another project never appears"
    by_run = {p["run_id"]: p for p in body["history"]}
    assert set(by_run) == {str(world.cur.id), str(world.prev.id)}
    cur_ai, prev_ai = by_run[str(world.cur.id)]["ai"], by_run[str(world.prev.id)]["ai"]
    assert (cur_ai["category"], cur_ai["version_count"], cur_ai["test_id"]) == (
        "AUTOMATION_DEFECT", 3, str(world.cur_pay.id),
    )
    assert (prev_ai["category"], prev_ai["version_count"], prev_ai["analysis_mode"]) == ("INFRASTRUCTURE", 1, "rules")
    assert body["ai_changes"] == [{
        "run_id": str(world.cur.id), "run_label": by_run[str(world.cur.id)]["run_label"],
        "from_category": "INFRASTRUCTURE", "to_category": "AUTOMATION_DEFECT",
        "from_confidence": 40, "to_confidence": 85, "kinds": ["category", "root_cause"],
    }]


async def test_the_canonical_page_carries_ai_per_run(world):
    resp = await _get(world, f"/api/v1/canonical-test-cases/{world.canonical.id}/runs", world.viewer)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ai_truncated"] is False
    assert [(i["test_run_id"], i["ai"]["category"], i["ai"]["version_count"]) for i in body["items"]] == [
        (str(world.cur.id), "AUTOMATION_DEFECT", 3), (str(world.prev.id), "INFRASTRUCTURE", 1),
    ]
    assert set(body["items"][0]["ai"]) == {
        "category", "confidence", "analysis_mode", "model", "requires_human_review", "root_cause_excerpt",
        "version_count",
    }


async def test_the_history_lookup_reads_two_indexed_keys(world):
    """The hot history route's addition is two key lookups (§11.3 G-B 4): EXPLAIN shows an index for both."""
    from sqlalchemy.dialects import postgresql

    from app.services.ai_history_read_service import test_history_ai

    statements: list = []

    class _Recorder:
        def __init__(self, session):
            self._session = session

        async def execute(self, stmt, *a, **k):
            statements.append(stmt)
            return await self._session.execute(stmt, *a, **k)

    async with world.sessions() as db:
        found = await test_history_ai(_Recorder(db), [world.cur_pay.id, world.prev_pay.id])
        assert set(found) == {world.cur_pay.id, world.prev_pay.id} and len(statements) == 2
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        for stmt in statements:
            sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
            plan = "\n".join(row[0] for row in (await db.execute(text("EXPLAIN " + sql))).all())
            assert "Index" in plan and "Seq Scan" not in plan, plan
        await db.rollback()


# ── 7. The one-shot backfill, end to end ─────────────────────────────────────


async def test_the_backfill_versions_old_runs_once_and_skips_what_a_pipeline_versioned(world, monkeypatch):
    """The real task pass over real stores: resumes from its cursor, writes a digest for an analysed run and a
    summary from ``run_summaries``, skips a run a pipeline already versioned, and a second pass writes nothing."""
    import redis.asyncio as redis_asyncio

    from app.db.mongo import Collections
    from app.models.postgres import AgentPipelineRun, Project
    from app.worker import tasks

    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        pytest.skip("REDIS_URL is not configured")
    redis = redis_asyncio.Redis.from_url(url, decode_responses=True)
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
    key = tasks.AI_HISTORY_BACKFILL_CURSOR_KEY
    p3, pipe = uuid.uuid4(), uuid.uuid4()
    ancient = datetime(2001, 1, 1, tzinfo=timezone.utc)
    start = json.dumps({"created_at": (ancient - timedelta(days=1)).isoformat(), "id": str(uuid.UUID(int=0))})
    mongo = world.mongo
    try:
        async with world.sessions() as db:
            db.add(Project(id=p3, name=f"aih-p3-{world.tag}", slug=f"aih-p3-{world.tag}", is_active=True))
            await db.flush()
            runs = []
            for i, name in enumerate(("analysed", "versioned", "empty", "summarised")):
                run = _run(p3, f"bf-{name}", minutes_ago=0)
                run.created_at = ancient + timedelta(hours=i)
                runs.append(run)
            db.add_all(runs)
            await db.flush()
            analysed, versioned, empty, summarised = runs
            cases = [_case(analysed, "test_a", "fp-a"), _case(versioned, "test_b", "fp-b")]
            db.add_all(cases)
            db.add(AgentPipelineRun(id=pipe, test_run_id=versioned.id, workflow_type="offline", status="completed",
                                    completed_at=ancient))
            # Committed first: the version writer locks the run row from its own session.
            await db.commit()
        async with world.sessions() as db:
            db.add(_analysis(cases[0], "PRODUCT_BUG", 70, "Old failure", "rules"))
            row = _analysis(cases[1], "PRODUCT_BUG", 75, "Pipeline already versioned this", "llm")
            db.add(row)
            await _version(db, mongo, versioned, pipe, [row])
        await mongo[Collections.RUN_SUMMARIES].insert_one({
            "test_run_id": str(summarised.id), "executive_summary": "An old summary.", "fallback_used": True,
            "markdown_report": "## overwritten by the decision report", "generated_at": ancient,
        })
        await redis.set(key, start)

        first = await tasks._backfill_ai_run_versions_pass("bf-1", chunk_size=2, max_chunks=1, time_budget_s=60)
        second = await tasks._backfill_ai_run_versions_pass("bf-2", chunk_size=2, max_chunks=1, time_budget_s=60)
        assert (first["status"], first["scanned"]) == ("max_chunks", 2)
        assert second["scanned"] == 4, "the second pass resumed after the first's last run"

        docs = await mongo[Collections.AI_RUN_VERSIONS].find(
            {"project_id": str(p3)}, {"_id": 0, "test_run_id": 1, "kind": 1, "trigger": 1, "version": 1, "payload": 1},
        ).to_list(length=50)
        by_run: dict = {}
        for doc in docs:
            by_run.setdefault(doc["test_run_id"], []).append((doc["kind"], doc["trigger"], doc["version"]))
        assert by_run[str(analysed.id)] == [("analysis_digest", "backfill", 1)]
        assert by_run[str(versioned.id)] == [("analysis_digest", "pipeline", 1)], "a live pipeline's version wins"
        assert str(empty.id) not in by_run
        assert by_run[str(summarised.id)] == [("run_summary", "backfill", 1)]
        summary = next(d for d in docs if d["kind"] == "run_summary")
        assert summary["payload"]["markdown_report"] is None
        assert summary["payload"]["markdown_source"] == "overwritten_by_decision_report"

        await redis.set(key, start)  # from the start again: idempotent
        again = await tasks._backfill_ai_run_versions_pass("bf-3", chunk_size=4, max_chunks=1, time_budget_s=60)
        assert again["scanned"] == 4 and again["written"] == 0
        assert await mongo[Collections.AI_RUN_VERSIONS].count_documents({"project_id": str(p3)}) == len(docs)
    finally:
        await redis.delete(key)
        await mongo[Collections.AI_RUN_VERSIONS].delete_many({"project_id": str(p3)})
        await mongo[Collections.RUN_SUMMARIES].delete_many({"generated_at": ancient})
        async with world.sessions.begin() as db:
            await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": p3})
        await redis.aclose()
