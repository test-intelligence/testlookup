"""W14 AGT-07: the Deep Investigation page's three offline reads, on real PostgreSQL.

* pre-scan: the groups equal ``GET /analytics/failure-groups``' signature
  grouping for the same run; passed / skipped results excluded;
  ``groups_total`` is the COUNT, not ``len(groups)``; the refinement with and
  without a current cluster snapshot; <= 2 statements; another project's
  member -> 403;
* history: ONE statement + the run-number map; another project -> 403, none ->
  422; the LATEST top-level deep pipeline per run; confidence 91 -> 0.91;
  ``cost_usd`` None when no stage reported one; ``clusters`` None without a
  snapshot (the CURRENT snapshot counted, not an older one); ``total`` across
  pages;
* estimate: no sample -> ``measured: false`` / ``minutes_p50 None``; three
  pipelines -> their p50s with the Root Cause stage scaled per failure; an
  ``ollama`` endpoint -> ``local_model`` / ``usd 0.0``; an unpriced model ->
  ``priced: false`` with ``llm_unpriced_calls_total`` unchanged; the sample is
  ONE statement; timed on a 20-pipeline sample.

None writes. Every time is built inside the test from one ``now``.
"""
from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, event, select

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

RCA = "agent.root_cause_analysis.v1"
TIMEOUT_A = "TimeoutError: checkout did not answer after 31s"
TIMEOUT_B = "TimeoutError: checkout did not answer after 45s"   # the same signature
NPE = "NullPointerException at PaymentService.charge(PaymentService.java:88)\n\tat x.y"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@contextmanager
def counting(engine):
    statements: list[str] = []

    def _record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)


@pytest.fixture
async def world():
    dsn = _dsn()
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import postgres as app_pg
    from app.models.postgres import (
        AgentConfig,
        AgentPipelineRun,
        AgentStageResult,
        DeepFinding,
        FailureCluster,
        LaunchStatus,
        Project,
        ProjectMember,
        TestCase,
        TestRun,
        User,
    )

    await app_pg.dispose_engine_for_loop()
    engine = create_async_engine(dsn)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    tag = uuid.uuid4().hex[:8]
    p, q = uuid.uuid4(), uuid.uuid4()
    viewer = User(id=uuid.uuid4(), email=f"v-{tag}@example.test", username=f"v-{tag}",
                  hashed_password="x", role="VIEWER", is_active=True)
    stranger = User(id=uuid.uuid4(), email=f"s-{tag}@example.test", username=f"s-{tag}",
                    hashed_password="x", role="QA_ENGINEER", is_active=True)
    async with sessions() as db:
        db.add_all([
            Project(id=p, name=f"w14c-p-{tag}", slug=f"w14c-p-{tag}"),
            Project(id=q, name=f"w14c-q-{tag}", slug=f"w14c-q-{tag}"),
            viewer, stranger,
        ])
        await db.flush()
        db.add_all([
            ProjectMember(user_id=viewer.id, project_id=p, role="VIEWER"),
            ProjectMember(user_id=stranger.id, project_id=q, role="QA_ENGINEER"),
        ])
        await db.commit()

    created: list[uuid.UUID] = []

    async def add_run(*, at: datetime, cases: list[tuple[str, str | None, str]] = (), project=None):
        run_id = uuid.uuid4()
        failing = sum(1 for status, _msg, _suite in cases if status in ("FAILED", "BROKEN"))
        async with sessions() as db:
            db.add(TestRun(
                id=run_id, project_id=project or p, build_number=f"b-{tag}-{len(created)}",
                status=LaunchStatus.FAILED, branch="main", primary_suite_name="checkout",
                total_tests=len(cases), passed_tests=0, failed_tests=failing, broken_tests=0,
                skipped_tests=0, unknown_tests=0, pass_rate=0.0, created_at=at,
            ))
            await db.flush()
            for i, (status, message, suite) in enumerate(cases):
                db.add(TestCase(
                    id=uuid.UUID(int=(run_id.int & ~0xFFFF) | (i + 1)), test_run_id=run_id,
                    test_name=f"test_{i}", suite_name=suite, status=status, error_message=message,
                    test_fingerprint=f"w14c{run_id.hex[:8]}{i:03d}",
                ))
            await db.commit()
        created.append(run_id)
        return run_id

    async def add_pipeline(run_id, *, status="completed", workflow_type="deep", started=None,
                           seconds=None, parent=None, created_at=None, stages=()):
        pid = uuid.uuid4()
        async with sessions() as db:
            db.add(AgentPipelineRun(
                id=pid, test_run_id=run_id, workflow_type=workflow_type, status=status,
                started_at=started, completed_at=(started + timedelta(seconds=seconds)) if started and seconds else None,
                parent_pipeline_run_id=parent, created_at=created_at or started or now,
            ))
            await db.flush()
            for stage in stages:
                db.add(AgentStageResult(pipeline_run_id=pid, **stage))
            await db.commit()
        return pid

    async def add_rows(*rows) -> None:
        async with sessions() as db:
            db.add_all(list(rows))
            await db.commit()

    try:
        yield SimpleNamespace(
            engine=engine, sessions=sessions, now=now, p=p, q=q, viewer=viewer, stranger=stranger,
            add_run=add_run, add_pipeline=add_pipeline, add_rows=add_rows,
        )
    finally:
        async with sessions() as db:
            runs = select(TestRun.id).where(TestRun.project_id.in_([p, q]))
            pipes = select(AgentPipelineRun.id).where(AgentPipelineRun.test_run_id.in_(runs))
            await db.execute(delete(AgentStageResult).where(AgentStageResult.pipeline_run_id.in_(pipes)))
            await db.execute(delete(FailureCluster).where(FailureCluster.test_run_id.in_(runs)))
            await db.execute(delete(AgentPipelineRun).where(AgentPipelineRun.test_run_id.in_(runs)))
            await db.execute(delete(DeepFinding).where(DeepFinding.test_run_id.in_(runs)))
            await db.execute(delete(TestCase).where(TestCase.test_run_id.in_(runs)))
            await db.execute(delete(TestRun).where(TestRun.project_id.in_([p, q])))
            await db.execute(delete(AgentConfig).where(AgentConfig.project_id.in_([p, q])))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([p, q])))
            await db.execute(delete(Project).where(Project.id.in_([p, q])))
            await db.execute(delete(User).where(User.id.in_([viewer.id, stranger.id])))
            await db.commit()
        await engine.dispose()
        await app_pg.dispose_engine_for_loop()


async def _get(world, user, path: str, params=None):
    """The deep router alone, its sessions from the world's engine, as ``user``."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import deep_investigation

    app = FastAPI()
    app.include_router(deep_investigation.router)

    async def _db():
        async with world.sessions() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.get(path, params=params)


def _cases() -> list[tuple[str, str | None, str]]:
    """Four timeouts (two raw lines, ONE signature), three NPEs, two with no
    message; a passed and a skipped result that carry a message (excluded)."""
    return [
        ("FAILED", TIMEOUT_A, "checkout"), ("FAILED", TIMEOUT_B, "checkout"),
        ("BROKEN", TIMEOUT_A, "payments"), ("FAILED", TIMEOUT_B, "checkout"),
        ("FAILED", NPE, "payments"), ("FAILED", NPE, "payments"), ("BROKEN", NPE, "payments"),
        ("FAILED", None, "checkout"), ("FAILED", "", "checkout"),
        ("PASSED", TIMEOUT_A, "checkout"), ("SKIPPED", NPE, "payments"),
    ]


# ── pre-scan ─────────────────────────────────────────────────────────────────


async def test_pre_scan_groups_by_signature_like_the_failure_groups_read(world):
    from app.models.postgres import TestRun
    from app.services import failure_groups_service
    from app.services.analytics_scope import AnalyticsScope
    from app.services.deep_investigation_insights_service import pre_scan
    from app.services.failure_signature import NO_MESSAGE_ID

    run_id = await world.add_run(at=world.now - timedelta(hours=1), cases=_cases())
    async with world.sessions() as db:
        run = await db.get(TestRun, run_id)
        with counting(world.engine) as statements:
            body = await pre_scan(db, run, limit=10)
        groups_read = await failure_groups_service.build_failure_groups(
            db,
            AnalyticsScope(project_id=world.p, allowed_project_ids=None, release_ids=(),
                           suite_names=(), days=30),
            include=frozenset(), now=world.now,
        )

    assert len(statements) <= 2, statements
    assert body["measured"] is True
    assert body["failing_results"] == 9                      # passed / skipped excluded
    assert body["groups_total"] == len(body["groups"]) == 3
    by_id = {g["signature_id"]: g for g in body["groups"]}
    # Parity: the same signatures and counts as GET /analytics/failure-groups.
    for group in groups_read["groups"]:
        assert by_id[group["signature"]]["members"] == group["failure_count"]
    assert by_id[NO_MESSAGE_ID]["members"] == groups_read["no_message"]["failure_count"] == 2
    assert sum(g["members"] for g in body["groups"]) == groups_read["total_failures"] == 9
    timeout = body["groups"][0]
    assert timeout["members"] == 4 and timeout["representative_error"] == TIMEOUT_A
    assert timeout["suites"] == ["checkout", "payments"]
    assert timeout["member_result_ids"] == sorted(timeout["member_result_ids"])
    assert by_id[NO_MESSAGE_ID]["representative_error"] is None
    assert body["refinement"] == "signature_only"
    assert all(g["cluster_id"] is None for g in body["groups"])


async def test_groups_total_is_a_count_and_the_snapshot_refines(world):
    from app.models.postgres import FailureCluster, TestRun
    from app.services.deep_investigation_insights_service import pre_scan

    run_id = await world.add_run(at=world.now - timedelta(hours=1), cases=_cases())
    async with world.sessions() as db:
        run = await db.get(TestRun, run_id)
        first_two = await pre_scan(db, run, limit=2)
    assert (first_two["groups_total"], len(first_two["groups"])) == (3, 2)

    timeout_ids = first_two["groups"][0]["member_result_ids"]
    old_pipeline = await world.add_pipeline(run_id, started=world.now - timedelta(hours=1), seconds=60)
    new_pipeline = await world.add_pipeline(run_id, started=world.now - timedelta(minutes=30), seconds=60)
    await world.add_rows(
        # an OLDER snapshot that put the NPEs in its cluster: not the current one
        FailureCluster(test_run_id=run_id, pipeline_run_id=old_pipeline, cluster_id="cl_old",
                       label="stale", member_test_ids=first_two["groups"][1]["member_result_ids"],
                       size=3, created_at=world.now - timedelta(hours=1)),
        FailureCluster(test_run_id=run_id, pipeline_run_id=new_pipeline, cluster_id="cl_001",
                       label="Checkout timeouts", member_test_ids=timeout_ids[:3], size=3,
                       created_at=world.now - timedelta(minutes=29)),
    )
    async with world.sessions() as db:
        refined = await pre_scan(db, await db.get(TestRun, run_id), limit=10)
    assert refined["refinement"] == "clusters"
    assert (refined["groups"][0]["cluster_id"], refined["groups"][0]["cluster_label"]) == (
        "cl_001", "Checkout timeouts",
    )
    assert refined["groups"][1]["cluster_id"] is None    # the stale snapshot is not read


async def test_pre_scan_of_a_run_with_no_failure_is_unmeasured(world):
    from app.models.postgres import TestRun
    from app.services.deep_investigation_insights_service import pre_scan

    run_id = await world.add_run(at=world.now, cases=[("PASSED", None, "checkout")])
    async with world.sessions() as db:
        body = await pre_scan(db, await db.get(TestRun, run_id), limit=10)
    assert (body["measured"], body["failing_results"], body["groups_total"], body["groups"]) == (
        False, 0, 0, [],
    )


async def test_pre_scan_refuses_another_projects_member(world):
    run_id = await world.add_run(at=world.now, cases=_cases())
    refused = await _get(world, world.stranger, f"/api/v1/deep-investigate/{run_id}/pre-scan")
    # require_run_access answers a non-member like a missing run (no oracle).
    assert (refused.status_code, refused.json()["detail"]) == (404, "Test run not found")
    allowed = await _get(world, world.viewer, f"/api/v1/deep-investigate/{run_id}/pre-scan",
                         params={"limit": 2})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["groups_total"] == 3
    assert (await _get(world, world.viewer, f"/api/v1/deep-investigate/{run_id}/pre-scan",
                       params={"limit": 51})).status_code == 422


# ── history ──────────────────────────────────────────────────────────────────


async def _history_world(world) -> dict:
    """Three investigated runs, one offline-only run, and Q's run (never listed)."""
    from app.models.postgres import DeepFinding, FailureCluster

    now = world.now
    r1 = await world.add_run(at=now - timedelta(hours=3), cases=_cases())
    r2 = await world.add_run(at=now - timedelta(hours=2), cases=_cases()[:3])
    r3 = await world.add_run(at=now - timedelta(hours=1), cases=_cases()[:1])
    r4 = await world.add_run(at=now - timedelta(minutes=30), cases=_cases()[:1])
    rq = await world.add_run(at=now, cases=_cases()[:1], project=world.q)
    # r1: an older failed deep pipeline, then the latest one (completed, costs reported)
    await world.add_pipeline(r1, status="failed", started=now - timedelta(hours=3), seconds=30)
    latest_r1 = await world.add_pipeline(r1, started=now - timedelta(hours=2, minutes=50), seconds=120, stages=[
        {"stage_name": "ingestion", "status": "completed", "cost_usd": 0.0},
        {"stage_name": "root_cause_analysis", "status": "completed", "cost_usd": 0.0125},
        {"stage_name": "summary", "status": "completed", "cost_usd": None},
    ])
    # r2: one deep pipeline whose stages reported no cost, plus a CHILD investigation (not listed)
    latest_r2 = await world.add_pipeline(r2, status="running", started=now - timedelta(hours=1, minutes=50), stages=[
        {"stage_name": "ingestion", "status": "completed"},
    ])
    await world.add_pipeline(r2, parent=latest_r2, started=now - timedelta(hours=1, minutes=40), seconds=10)
    latest_r3 = await world.add_pipeline(r3, started=now - timedelta(minutes=50), seconds=60)
    await world.add_pipeline(r4, workflow_type="offline", started=now - timedelta(minutes=20), seconds=60)
    await world.add_pipeline(rq, started=now - timedelta(minutes=5), seconds=60)
    stale = await world.add_pipeline(r1, status="failed", workflow_type="offline",
                                     started=now - timedelta(hours=4), seconds=5)
    await world.add_rows(
        FailureCluster(test_run_id=r1, pipeline_run_id=stale, cluster_id="cl_a", label="a",
                       member_test_ids=[], size=1, created_at=now - timedelta(hours=4)),
        FailureCluster(test_run_id=r1, pipeline_run_id=stale, cluster_id="cl_b", label="b",
                       member_test_ids=[], size=1, created_at=now - timedelta(hours=4)),
        FailureCluster(test_run_id=r1, pipeline_run_id=stale, cluster_id="cl_c", label="c",
                       member_test_ids=[], size=1, created_at=now - timedelta(hours=4)),
        FailureCluster(test_run_id=r1, pipeline_run_id=latest_r1, cluster_id="cl_1", label="1",
                       member_test_ids=[], size=2, created_at=now - timedelta(hours=2, minutes=48)),
        FailureCluster(test_run_id=r1, pipeline_run_id=latest_r1, cluster_id="cl_2", label="2",
                       member_test_ids=[], size=1, created_at=now - timedelta(hours=2, minutes=48)),
        DeepFinding(test_run_id=r1, cluster_id="cl_1", confidence_score=91),
        DeepFinding(test_run_id=r2, cluster_id="cl_x", confidence_score=None),
    )
    return {"r1": r1, "r2": r2, "r3": r3, "r4": r4, "rq": rq,
            "latest_r1": latest_r1, "latest_r2": latest_r2, "latest_r3": latest_r3}


async def test_history_is_one_statement_with_the_latest_deep_pipeline_per_run(world):
    from app.services.deep_investigation_insights_service import deep_history

    w = await _history_world(world)
    async with world.sessions() as db:
        with counting(world.engine) as statements:
            body = await deep_history(db, world.p, page=1, size=10)

    history = [s for s in statements if "DISTINCT ON" in s]
    assert len(history) == 1, statements
    print(f"\nSTATS history statements={len(statements)} (1 history + {len(statements) - 1} run-number map)")
    assert body["total"] == 3                               # r4 (offline only) and Q's run absent
    assert [row["run_id"] for row in body["items"]] == [w["r3"], w["r2"], w["r1"]]   # newest run first
    rows = {row["run_id"]: row for row in body["items"]}
    r1, r2, r3 = rows[w["r1"]], rows[w["r2"]], rows[w["r3"]]
    assert r1["pipeline_run_id"] == w["latest_r1"] and r1["status"] == "completed"
    assert r1["duration_seconds"] == 120.0
    assert r1["cost_usd"] == pytest.approx(0.0125)          # 0.0 + 0.0125; the NULL stage skipped
    assert r1["clusters"] == 2                              # the CURRENT snapshot, not the stale 3
    assert (r1["findings"], r1["avg_confidence"]) == (1, 0.91)
    assert r2["pipeline_run_id"] == w["latest_r2"]          # the child investigation is not "the latest"
    assert (r2["public_status"], r2["duration_seconds"]) == ("in_progress", None)
    assert r2["cost_usd"] is None                           # no stage reported a cost: unknown, not $0
    assert (r2["findings"], r2["avg_confidence"]) == (1, None)
    assert (r3["clusters"], r3["findings"], r3["avg_confidence"]) == (None, 0, None)
    assert all(row["run_seq"] is not None for row in body["items"])


async def test_history_pages_keep_the_total(world):
    from app.services.deep_investigation_insights_service import deep_history

    await _history_world(world)
    async with world.sessions() as db:
        pages = [await deep_history(db, world.p, page=n, size=2) for n in (1, 2, 3)]
    assert [len(page["items"]) for page in pages] == [2, 1, 0]
    assert [page["total"] for page in pages] == [3, 3, 3]


async def test_history_needs_a_project_the_caller_can_read(world):
    from app.core.agent_ops_vocab import HISTORY_NEEDS_PROJECT

    await _history_world(world)
    path = "/api/v1/deep-investigate/history"
    missing = await _get(world, world.viewer, path)
    assert (missing.status_code, missing.json()["detail"]) == (422, HISTORY_NEEDS_PROJECT)
    foreign = await _get(world, world.viewer, path, params={"project_id": str(world.q)})
    assert (foreign.status_code, foreign.json()["detail"]) == (403, "You do not have access to this project")
    bad = await _get(world, world.viewer, path, params={"project_id": "not-a-uuid"})
    assert (bad.status_code, bad.json()["detail"]) == (400, "Invalid project ID")
    ok = await _get(world, world.viewer, path, params={"project_id": str(world.p), "size": 2})
    assert ok.status_code == 200, ok.text
    assert (ok.json()["total"], len(ok.json()["items"])) == (3, 2)


# ── estimate ─────────────────────────────────────────────────────────────────


@pytest.fixture
def estimate_env(monkeypatch, world):
    """A fixed global AI config and gate (the gate has its own test file), and
    the project's Root Cause endpoint set per test."""
    from app.services import ai_config_resolver, deep_investigation_gate
    from app.services.agent_config_service import AgentConfigV1, default_config, put_config

    ai = {"provider": "ollama", "model": "qwen2.5:7b", "offline_mode": False}

    async def _config(**_kwargs):
        return dict(ai)

    async def _gate(_db):
        return deep_investigation_gate.DeepGateDecision(True, None, None, "llm", True, "ai_config")

    monkeypatch.setattr(ai_config_resolver, "get_effective_ai_config", _config)
    monkeypatch.setattr(deep_investigation_gate, "deep_investigation_gate", _gate)

    async def endpoint(provider: str, model: str) -> None:
        doc = default_config(RCA).model_dump()
        doc["model"]["tier"] = "slm"
        doc["model"]["slm"] = {"provider": provider, "model": model}
        async with world.sessions() as db:
            await put_config(db, world.p, AgentConfigV1.model_validate(doc), updated_by=None)
            await db.commit()

    endpoint.ai = ai  # type: ignore[attr-defined]  # the global config a test may change
    return endpoint


def _timed(stage: str, start: datetime, seconds: float, **extra) -> dict:
    return {"stage_name": stage, "status": "completed", "started_at": start,
            "completed_at": start + timedelta(seconds=seconds), **extra}


async def _sample(world, k: int, *, status: str = "completed", workflow_type: str = "deep") -> uuid.UUID:
    """Pipeline k: 60k s long; ingestion saw k failures; Root Cause 10 s per failure."""
    run_id = await world.add_run(at=world.now - timedelta(days=1, hours=k), cases=_cases()[:1])
    start = world.now - timedelta(days=1, hours=k)
    return await world.add_pipeline(run_id, status=status, workflow_type=workflow_type,
                                    started=start, seconds=60 * k, stages=[
        _timed("ingestion", start, 1, result_data={"total_tests": 9, "failed_count": k}),
        _timed("root_cause_analysis", start, 10 * k, input_tokens=100 * k, output_tokens=10 * k),
        _timed("summary", start, 4 + k, input_tokens=50 + 10 * k, output_tokens=5 + k),
    ])


async def _estimate(world, run_id, **configuration):
    from app.models.postgres import TestRun
    from app.services.deep_investigation_insights_service import deep_estimate
    from app.services.deep_run_overrides import DeepRunConfiguration

    async with world.sessions() as db:
        run = await db.get(TestRun, run_id)
        with counting(world.engine) as statements:
            body = await deep_estimate(db, run, configuration=DeepRunConfiguration(**configuration))
    return body, statements


async def test_no_sample_is_unmeasured_never_zero(world, estimate_env):
    from app.core.agent_ops_vocab import ESTIMATE_LOCAL_NOTE, ESTIMATE_UNMEASURED

    await estimate_env("ollama", "qwen2.5:7b")
    target = await world.add_run(at=world.now, cases=_cases())
    await _sample(world, 1, status="failed")                    # not a sample: failed
    await _sample(world, 2, workflow_type="offline")            # not a sample: offline

    body, _ = await _estimate(world, target)

    assert (body["measured"], body["reason"], body["sample_pipelines"]) == (False, ESTIMATE_UNMEASURED, 0)
    assert body["minutes_p50"] is None and body["tokens_p50"] is None and body["stages"] == []
    # A local model is measured as free even without a sample.
    assert (body["cost"]["priced"], body["cost"]["local_model"], body["cost"]["usd"]) == (True, True, 0.0)
    assert body["cost"]["note"] == ESTIMATE_LOCAL_NOTE
    assert body["configuration"]["failures_analysed"] == 9 and body["gate"]["allowed"] is True


async def test_three_pipelines_give_their_p50s_scaled_per_failure(world, estimate_env):
    await estimate_env("ollama", "qwen2.5:7b")
    target = await world.add_run(at=world.now, cases=_cases())           # 9 failing results
    await _sample(world, 1)
    await _sample(world, 2, status="passed")
    await _sample(world, 3)

    body, statements = await _estimate(world, target)

    assert [s for s in statements if "percentile_cont" in s].__len__() == 1   # the sample: ONE statement
    print(f"\nSTATS estimate statements={len(statements)}")
    assert (body["measured"], body["sample_pipelines"]) == (True, 3)
    stages = {s["stage_name"]: s for s in body["stages"]}
    rca, summary = stages["root_cause_analysis"], stages["summary"]
    # Root Cause: 10 s / 100 in / 10 out per failure, x 9 failures analysed.
    assert (rca["per_failure"], rca["duration_seconds_p50"]) == (True, 90.0)
    assert (rca["input_tokens_p50"], rca["output_tokens_p50"]) == (900, 90)
    assert (summary["per_failure"], summary["duration_seconds_p50"]) == (False, 6.0)
    assert (summary["input_tokens_p50"], summary["output_tokens_p50"], summary["samples"]) == (70, 7, 3)
    # The pipeline's p50 (120 s) moved by what the scaling adds (90 - 20 s).
    assert body["minutes_p50"] == round((120 + 70) / 60, 1)
    assert body["tokens_p50"] == 900 + 90 + 70 + 7
    assert (body["cost"]["local_model"], body["cost"]["usd"]) == (True, 0.0)

    narrowed, _ = await _estimate(world, target, selected_failures=2)
    assert narrowed["configuration"]["failures_analysed"] == 2
    assert {s["stage_name"]: s for s in narrowed["stages"]}["root_cause_analysis"]["duration_seconds_p50"] == 20.0


async def test_an_unpriced_model_is_never_priced_and_never_counted(world, estimate_env, monkeypatch):
    from app.core.agent_ops_vocab import ESTIMATE_UNPRICED
    from app.core.metrics import llm_unpriced_calls_total

    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)       # a remote model may run here
    monkeypatch.setattr(settings, "AI_LLM_PROVIDER_ALLOWLIST", "")
    await estimate_env("openai", "gpt-w14-unpriced")
    target = await world.add_run(at=world.now, cases=_cases())
    await _sample(world, 1)
    counter = llm_unpriced_calls_total.labels(provider="openai", model="gpt-w14-unpriced")
    before = counter._value.get()

    body, _ = await _estimate(world, target)

    assert body["measured"] is True
    assert (body["cost"]["priced"], body["cost"]["usd"], body["cost"]["local_model"]) == (False, None, False)
    assert body["cost"]["note"] == ESTIMATE_UNPRICED.format(provider="openai", model="gpt-w14-unpriced")
    assert counter._value.get() == before


async def test_a_tier_the_offline_ceiling_removed_has_no_price(world, estimate_env, monkeypatch):
    from app.core.agent_ops_vocab import ESTIMATE_NO_ENDPOINT
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    estimate_env.ai.update({"provider": "", "model": ""})     # no global model to fall back on
    await estimate_env("openai", "gpt-4o-mini")
    target = await world.add_run(at=world.now, cases=_cases())

    body, _ = await _estimate(world, target)

    assert (body["cost"]["priced"], body["cost"]["usd"]) == (False, None)
    assert body["cost"]["note"] == ESTIMATE_NO_ENDPOINT.format(tier="slm")


async def test_the_estimate_on_a_twenty_pipeline_sample(world, estimate_env):
    await estimate_env("ollama", "qwen2.5:7b")
    target = await world.add_run(at=world.now, cases=_cases())
    for k in range(1, 23):                                   # 22 completed: the latest 20 are read
        await _sample(world, k)

    started = time.perf_counter()
    body, statements = await _estimate(world, target)
    elapsed_ms = (time.perf_counter() - started) * 1000
    print(f"\nSTATS estimate on a {body['sample_pipelines']}-pipeline sample: "
          f"{elapsed_ms:.1f} ms, {len(statements)} statements")
    assert body["sample_pipelines"] == 20
