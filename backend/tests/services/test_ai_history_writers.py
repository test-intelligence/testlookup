"""AIH-01: every ``ai_analysis`` / run-summary writer also records an immutable version.

Unit half (no database): each of the writers W1-W6 of the W03 contract calls
its history helper in the right place (inside its own transaction, before its
commit, with the right trigger and actor), the pipeline upsert's ``set_`` now
rewrites ``llm_provider`` / ``llm_model`` / ``created_at`` (the confirmed bug),
and a history failure never fails the write around it.

Live half (PostgreSQL + MongoDB; skips without ``TESTLOOKUP_POSTGRES_TEST_DSN``):
the pipeline runs twice with analyses produced through the REAL ``BudgetedLLM``
wrapper around a scripted chat model (first a missing model, so the rules
fallback answers; then a scripted LLM answer). ``ai_analysis`` keeps one row per
test, ``ai_analysis_versions`` gets two, ``ai_run_versions`` two docs of each
kind, ``run_summaries`` keeps its unique index and one doc, a broker
redelivery of the same pipeline adds nothing, the activity ledger gets
``analysis.completed`` twice and ``analysis.verdict_changed`` once, and a run
deleted before its summary is versioned leaves no orphan version.
"""
from __future__ import annotations

import ast
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

BACKEND = Path(__file__).resolve().parents[2]


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class _Journal:
    def __init__(self):
        self.calls: list[str] = []


def _session(journal: _Journal, executed: list):
    session = MagicMock()

    async def _execute(stmt, *_a, **_k):
        executed.append(stmt)
        journal.calls.append("execute")
        return MagicMock()

    async def _commit():
        journal.calls.append("commit")

    session.execute = AsyncMock(side_effect=_execute)
    session.commit = AsyncMock(side_effect=_commit)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ── W1: the pipeline upsert ──────────────────────────────────────────────────


async def _run_upsert(analyses, pipeline_run_id="11111111-1111-4111-8111-111111111111"):
    from app.agents import analysis_agent as mod

    journal, executed = _Journal(), []
    calls = []

    async def fake_stage(db, snapshots, **kwargs):
        journal.calls.append("stage")
        calls.append((snapshots, kwargs))

    with patch.object(mod, "AsyncSessionLocal", return_value=_session(journal, executed)), \
            patch("app.services.ai_history_service.stage_analysis_versions", fake_stage), \
            patch("app.services.cache_service.bump_analytics_epoch", AsyncMock()):
        await mod.AnalysisAgent()._batch_upsert_analyses(
            analyses, project_id="p", eval_manifest_checksum="sum", pipeline_run_id=pipeline_run_id
        )
    return journal, executed, calls


@pytest.mark.asyncio
async def test_the_pipeline_upsert_rewrites_the_model_and_the_time_on_conflict():
    """The confirmed ``set_`` bug: a re-analysis by another model kept naming
    the OLD model and time on the latest row."""
    tc = str(uuid.uuid4())
    _, executed, _ = await _run_upsert({tc: {"root_cause_summary": "x", "failure_category": "PRODUCT_BUG",
                                             "llm_provider": "openrouter", "llm_model": "m2"}})
    upserts = [s for s in executed if _sql(s).startswith("INSERT INTO ai_analysis ")]
    assert len(upserts) == 1
    update_clause = _sql(upserts[0]).split("DO UPDATE SET", 1)[1]
    for column in ("llm_provider", "llm_model", "created_at", "root_cause_summary", "routing_metadata"):
        assert f"{column} = " in update_clause, column
    params = upserts[0].compile(dialect=postgresql.dialect()).params
    assert "openrouter" in params.values() and "m2" in params.values()


@pytest.mark.asyncio
async def test_the_pipeline_upsert_stages_one_version_per_written_test_before_its_commit():
    good, error_only = str(uuid.uuid4()), str(uuid.uuid4())
    journal, _, calls = await _run_upsert({
        good: {"root_cause_summary": "db refused", "failure_category": "INFRASTRUCTURE", "confidence_score": 90,
               "_audit": {"analysis_mode": "rules"}},
        error_only: {"error": "boom"},  # skipped by the upsert, so by history too
    })
    (snapshots, kwargs), = calls
    assert [str(s.test_case_id) for s in snapshots] == [good]
    assert snapshots[0].routing_metadata == {"analysis_mode": "rules", "eval_manifest_checksum": "sum"}
    assert kwargs["trigger"] == "pipeline"
    assert kwargs["pipeline_run_id"] == uuid.UUID("11111111-1111-4111-8111-111111111111")
    assert kwargs["mongo"] is not None
    assert journal.calls.index("stage") < journal.calls.index("commit")


@pytest.mark.asyncio
async def test_each_chunk_stages_its_own_versions():
    analyses = {str(uuid.uuid4()): {"root_cause_summary": "x", "failure_category": "FLAKY"} for _ in range(120)}
    _, _, calls = await _run_upsert(analyses)
    assert [len(s) for s, _ in calls] == [50, 50, 20]


def test_the_stage_runs_its_pipeline_id_through():
    """``run()`` passes ``pipeline_run_id`` to the upsert (an AST check: running
    the whole stage needs the LLM router, the event log and the cost meter)."""
    tree = ast.parse((BACKEND / "app" / "agents" / "analysis_agent.py").read_text(encoding="utf-8"))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "_batch_upsert_analyses"
    ]
    assert calls and all(any(kw.arg == "pipeline_run_id" for kw in c.keywords) for c in calls)


@pytest.mark.asyncio
async def test_a_history_failure_never_fails_the_staging_call():
    from app.services.ai_history_service import snapshot_from_analysis_dict, stage_analysis_versions

    db = MagicMock()
    db.begin_nested = MagicMock(side_effect=RuntimeError("savepoint refused"))
    snap = snapshot_from_analysis_dict(uuid.uuid4(), {"failure_category": "FLAKY"}, None)
    result = await stage_analysis_versions(db, [snap], trigger="pipeline")
    assert result.failed == 1 and result.staged == 0


@pytest.mark.asyncio
async def test_a_bulk_history_failure_returns_zero_and_never_raises():
    from app.services.ai_history_service import stage_bulk_reclassify_versions

    db = MagicMock()
    db.begin_nested = MagicMock(side_effect=RuntimeError("savepoint refused"))
    assert await stage_bulk_reclassify_versions(db, [uuid.uuid4()], actor_user_id=None) == 0


# ── W6: the run summary ──────────────────────────────────────────────────────


def _structured(layer1="All good.", fallback=False):
    return {
        "layer1_executive_summary": layer1,
        "layer2_incident_view": {"what_failed": "4 tests"},
        "layer3_evidence_pack": {"citations": [{"id": 1}], "data_sources_used": ["stack_trace"]},
        "layer4_action_plan": {"immediate_mitigation": "look"},
        "executive_panel": {"headline": "x"},
        "_provenance": {"fallback_used": fallback, "model_config_snapshot": {"provider": "ollama", "model": "m"}},
    }


async def _store(record):
    from app.agents.summary_agent import SummaryAgent

    stored = {}

    async def _update_one(_filter, update, **_kw):
        stored.update(update["$set"])

    collection = MagicMock()
    collection.update_one = AsyncMock(side_effect=_update_one)
    mongo = MagicMock()
    mongo.__getitem__ = MagicMock(return_value=collection)
    state = {"project_id": "p", "pipeline_run_id": "pipe-1", "analyses": {"a": {}}, "anomalies": [1]}
    with patch("app.agents.summary_agent.get_mongo_db", MagicMock(return_value=mongo)), \
            patch("app.services.ai_history_service.record_run_version", record):
        await SummaryAgent()._store_summary("run-1", _structured(), state)
    return stored, mongo


@pytest.mark.asyncio
async def test_the_summary_is_versioned_with_exactly_the_stored_content():
    record = AsyncMock(return_value=None)
    stored, mongo = await _store(record)
    kwargs = record.await_args.kwargs
    assert record.await_args.args == (mongo,)
    assert (kwargs["test_run_id"], kwargs["project_id"], kwargs["pipeline_run_id"]) == ("run-1", "p", "pipe-1")
    assert kwargs["kind"] == "run_summary" and kwargs["trigger"] == "pipeline"
    payload = kwargs["payload"]
    assert set(payload) == {
        "executive_summary", "markdown_report", "layer1_executive_summary", "layer2_incident_view",
        "layer3_evidence_pack", "layer4_action_plan", "executive_panel", "anomaly_count", "is_regression",
        "analysis_count", "fallback_used", "citations", "summary_provenance",
    }
    assert {k: stored[k] for k in payload} == payload, "the $set (latest pointer) and the version agree"
    assert stored["schema_version"] == 4 and "generated_at" in stored
    assert kwargs["generated_at"] == stored["generated_at"]
    assert kwargs["meta"]["headline"] == "All good."


@pytest.mark.asyncio
async def test_mongo_down_while_versioning_never_fails_the_summary():
    from pymongo.errors import ServerSelectionTimeoutError

    stored, _ = await _store(AsyncMock(side_effect=ServerSelectionTimeoutError("down")))
    assert stored["layer1_executive_summary"] == "All good."


# ── W2: single-test analyze ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_test_analyze_stages_a_manual_version_before_its_commit(monkeypatch):
    from app.core.deps import AuthorizedTestCaseContext
    from app.models.schemas import AnalyzeRequest
    from app.routers import analyze as route

    journal: list[str] = []
    tc = SimpleNamespace(id=uuid.uuid4(), test_name="t", suite_name="s", error_message="boom", stack_trace=None,
                         duration_ms=1, severity="P2", test_fingerprint="fp")
    run = SimpleNamespace(end_time=None, start_time=None, ocp_pod_name=None, ocp_namespace=None)
    monkeypatch.setattr(route, "resolve_authorized_test_case", AsyncMock(return_value=AuthorizedTestCaseContext(
        test_case=tc, test_run=run, run_id=uuid.uuid4(), project_id=uuid.uuid4())))
    monkeypatch.setattr(route.analysis_router, "classify_test", AsyncMock(return_value={
        "root_cause_summary": "x", "failure_category": "PRODUCT_BUG", "confidence_score": 80,
        "evidence_references": [{"source": "s", "reference_id": "1", "excerpt": "x"}],
        "recommended_actions": [], "tools_used": [], "llm_provider": "ollama", "llm_model": "m",
        "is_flaky": False, "backend_error_found": False, "pod_issue_found": False,
        "_routing": {"mode_used": "llm"},
    }))
    staged = []

    async def fake_stage(db, snapshot, **kwargs):
        journal.append("stage")
        staged.append((snapshot, kwargs))

    monkeypatch.setattr(route, "stage_analysis_version", fake_stage)
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.commit = AsyncMock(side_effect=lambda: journal.append("commit"))
    user = SimpleNamespace(id=uuid.uuid4())
    await route.analyze_test_case(AnalyzeRequest(test_case_id=tc.id), db=db, current_user=user)
    (snapshot, kwargs), = staged
    assert kwargs["trigger"] == "manual_analyze" and kwargs["actor_user_id"] == user.id
    assert snapshot.test_case_id == tc.id and snapshot.failure_category == "PRODUCT_BUG"
    assert snapshot.routing_metadata["analysis_mode"] == "llm"
    assert journal == ["stage", "commit"]


# ── W3 / W4: human corrections ───────────────────────────────────────────────


def _correction():
    from app.models.postgres import FeedbackRating

    return SimpleNamespace(rating=FeedbackRating.INCORRECT, corrected_category="PRODUCT_BUG",
                           corrected_root_cause="the real cause", comment=None)


def _analysis():
    return SimpleNamespace(id=uuid.uuid4(), test_case_id=uuid.uuid4(), failure_category="INFRASTRUCTURE",
                           root_cause_summary="x", requires_human_review=True, confidence_score=40,
                           is_flaky=False, backend_error_found=False, pod_issue_found=False,
                           llm_provider="ollama", llm_model="m", routing_metadata={"analysis_mode": "llm"},
                           recommended_actions=None, evidence_references=None, tools_used=None, role_actions=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["submit", "edit"])
async def test_a_human_correction_stages_a_version_with_its_actor(path):
    from app.services import feedback_service

    analysis = _analysis()
    order: list[str] = []
    staged = []

    async def fake_stage(db, snapshot, **kwargs):
        order.append("stage")
        staged.append((snapshot, kwargs))

    async def human(*_a, **_k):
        order.append("human_category")

    feedback = SimpleNamespace(rating=None)
    results = [analysis] if path == "submit" else [feedback, analysis]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[
        MagicMock(scalar_one_or_none=MagicMock(return_value=r)) for r in results
    ])
    user = SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER")
    with patch.object(feedback_service, "_require_analysis_access", AsyncMock()), \
            patch.object(feedback_service, "apply_human_category", human), \
            patch("app.services.ai_history_service.stage_analysis_version", fake_stage):
        fn = feedback_service.submit_feedback if path == "submit" else feedback_service.update_feedback
        await fn(db, analysis.id, _correction(), user)
    (snapshot, kwargs), = staged
    assert kwargs["trigger"] == "human_correction" and kwargs["actor_user_id"] == user.id
    assert snapshot.failure_category == "PRODUCT_BUG" and snapshot.root_cause_summary == "the real cause"
    assert snapshot.requires_human_review is False
    assert order == ["human_category", "stage"]
    db.commit.assert_not_called()  # stage-only service


@pytest.mark.asyncio
async def test_plain_rating_feedback_stages_nothing():
    from app.models.postgres import FeedbackRating
    from app.services import feedback_service

    analysis = _analysis()
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=analysis)))
    stage = AsyncMock()
    body = SimpleNamespace(rating=FeedbackRating.CORRECT, corrected_category=None, corrected_root_cause=None, comment=None)
    with patch.object(feedback_service, "_require_analysis_access", AsyncMock()), \
            patch("app.services.ai_history_service.stage_analysis_version", stage):
        await feedback_service.submit_feedback(db, analysis.id, body, SimpleNamespace(id=uuid.uuid4(), role="VIEWER"))
    stage.assert_not_awaited()


# ── W5: bulk reclassify ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_reclassify_versions_after_its_update_and_before_its_commit(monkeypatch):
    from app.models.postgres import FailureCategory
    from app.models.schemas import ClassifyUncategorizedRequest
    from app.routers import analytics as route

    order: list[str] = []
    tc_ids = [uuid.uuid4(), uuid.uuid4()]

    async def _execute(stmt, *_a, **_k):
        sql = _sql(stmt)
        order.append(sql.split(" ")[0] + (" ai_analysis" if "UPDATE ai_analysis" in sql else ""))
        result = MagicMock()
        result.all.return_value = [(i,) for i in tc_ids]
        return result

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    staged = []

    async def fake_bulk(db_arg, ids, *, actor_user_id):
        order.append("stage")
        staged.append((ids, actor_user_id))
        return len(ids)

    monkeypatch.setattr(route, "get_accessible_project_ids", AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.ai_history_service.stage_bulk_reclassify_versions", fake_bulk)
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    user = SimpleNamespace(id=uuid.uuid4())
    payload = ClassifyUncategorizedRequest(project_id=uuid.uuid4(), category=FailureCategory.PRODUCT_BUG)
    response = await route.classify_uncategorized_failures(payload, db=db, current_user=user)
    assert response.updated == 2
    assert staged == [(tc_ids, user.id)]
    assert order == ["SELECT", "UPDATE", "UPDATE ai_analysis", "stage", "commit"]


# ── Live: the pipeline twice, through the real BudgetedLLM ───────────────────

FINAL = json.dumps({
    "root_cause_summary": "Checkout rounds the tax twice.",
    "failure_category": "PRODUCT_BUG",
    "backend_error_found": True,
    "pod_issue_found": False,
    "is_flaky": False,
    "confidence_score": 82,
    "recommended_actions": ["Fix the tax rounding"],
    "role_actions": {},
    "evidence_references": [],
})


@pytest.fixture
def gates(monkeypatch):
    """Stub only the wrapper's GATE stores (cost meter, breaker, audit sinks), never get_llm's product."""
    import app.services.agent as triage
    from app.core.config import settings
    from app.services import llm_circuit_breaker, llm_cost_reservation as cost
    from app.services.llm_cost_reservation import Reservation

    async def available(*_a, **_k):
        return None

    async def reserve(provider, model, *, input_tokens, max_output_tokens):
        return Reservation(key="k", project_id="p", estimated_usd=0.01, ttl_seconds=60)

    async def nothing(*_a, **_k):
        return None

    async def passes(score):
        return {"passed": True, "threshold": 70, "score": score}

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", available)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(nothing))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(nothing))
    monkeypatch.setattr(cost, "reserve", reserve)
    monkeypatch.setattr(cost, "settle", nothing)
    monkeypatch.setattr(triage, "_store_audit_trail", AsyncMock())
    monkeypatch.setattr(triage, "_emit_event", AsyncMock())
    monkeypatch.setattr(triage, "_get_tools", lambda: [])
    monkeypatch.setattr("app.services.confidence_gate.check_confidence", passes)
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    return settings


async def _triage_all(monkeypatch, case_ids, llm) -> dict:
    """One analysis per test through ``run_triage_agent`` with the real wrapper,
    then the ``_audit`` block AnalysisAgent.run derives from ``_routing``."""
    import app.services.agent as triage
    from app.core.config import settings

    async def get_llm(*_a, **_k):
        return llm

    monkeypatch.setattr(triage, "get_llm", get_llm)
    analyses = {}
    for i, cid in enumerate(case_ids):
        analysis = await triage.run_triage_agent(
            test_case_id=str(cid), test_name=f"test_case_{i}",
            error_message="AssertionError: total 100.10 != 100.00",
            skip_fast_classifier=True, skip_cache=True,
        )
        routing = analysis.get("_routing") or {}
        analysis["_audit"] = {
            "analysis_mode": routing.get("mode_used"),
            "fallback_from": routing.get("fallback_from"),
            "fallback_reason": routing.get("fallback_reason"),
            "model_config_snapshot": {"provider": settings.LLM_PROVIDER, "model": settings.LLM_MODEL},
        }
        analyses[str(cid)] = analysis
    return analyses


def _scripted(responses):
    from langchain_core.language_models import FakeListChatModel

    from app.services.llm_factory import BudgetedLLM

    return BudgetedLLM(FakeListChatModel(responses=responses), provider="openrouter", model="scripted")


def _missing_model():
    from langchain_core.language_models import FakeListChatModel

    from app.services.llm_factory import BudgetedLLM

    class ModelMissing(FakeListChatModel):
        async def ainvoke(self, *args, **kwargs):
            raise RuntimeError("model 'qwen2.5:7b' not found, try pulling it first")

    return BudgetedLLM(ModelMissing(responses=["x"]), provider="ollama", model="qwen2.5:7b")


@pytest.fixture
async def live_world():
    from tests.integration.ai_history_seed import drop_project, dsn, seed_pipeline, seed_project, seed_run

    dsn()
    from app.db import postgres as app_pg
    from app.db.mongo import Collections, get_mongo_db, reset_mongo_client

    await app_pg.dispose_engine_for_loop()
    reset_mongo_client()
    mongo = get_mongo_db()
    try:
        await mongo.command("ping")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MongoDB is not reachable: {exc}")
    async with app_pg.AsyncSessionLocal() as db:
        project = await seed_project(db, "gah-writers")
        run, cases = await seed_run(db, project, 2, build="777")
        pipelines = [await seed_pipeline(db, run) for _ in range(2)]
        await db.commit()
    yield SimpleNamespace(project=project, run=run, cases=cases, pipelines=pipelines, mongo=mongo,
                          factory=app_pg.AsyncSessionLocal)
    for coll in (Collections.AI_RUN_VERSIONS, Collections.RUN_SUMMARIES):
        await mongo[coll].delete_many({"test_run_id": str(run)})
    await drop_project(app_pg.AsyncSessionLocal, project)
    reset_mongo_client()
    await app_pg.dispose_engine_for_loop()


async def _pipeline(w, pipeline_id, analyses, verdict):
    """What one pipeline writes: the per-test upsert, the summary, the release
    decision, then the success hook's digest capture (the task commits)."""
    from sqlalchemy import select

    from app.agents.analysis_agent import AnalysisAgent
    from app.agents.summary_agent import SummaryAgent
    from app.models.postgres import ReleaseDecision
    from app.services.ai_history_service import capture_analysis_digest

    await AnalysisAgent()._batch_upsert_analyses(analyses, project_id=str(w.project), pipeline_run_id=str(pipeline_id))
    state = {"project_id": str(w.project), "pipeline_run_id": str(pipeline_id), "analyses": analyses,
             "build_number": "777"}
    structured = {
        "layer1_executive_summary": f"{verdict}: {len(analyses)} failures analysed",
        "layer2_incident_view": {}, "layer3_evidence_pack": {"citations": []}, "layer4_action_plan": {},
        "executive_panel": {}, "_provenance": {"fallback_used": verdict == "GO"},
    }
    await SummaryAgent()._store_summary(str(w.run), structured, state)
    async with w.factory() as db:
        row = (await db.execute(select(ReleaseDecision).where(ReleaseDecision.test_run_id == w.run))).scalar_one_or_none()
        if row is None:
            db.add(ReleaseDecision(test_run_id=w.run, pipeline_run_id=pipeline_id, recommendation=verdict,
                                   risk_score=30 if verdict == "GO" else 75, conditions_for_go=[]))
        else:
            row.pipeline_run_id, row.recommendation = pipeline_id, verdict
            row.risk_score = 30 if verdict == "GO" else 75
        await db.commit()
        result = await capture_analysis_digest(db, w.mongo, run_id=w.run, pipeline_run_id=pipeline_id,
                                               stage_quality="normal")
        await db.commit()
    return result


async def _counts(w):
    from sqlalchemy import func, select

    from app.db.mongo import Collections
    from app.models.postgres import AIAnalysis, AIAnalysisVersion, ProjectActivityEvent

    async with w.factory() as db:
        analyses = (await db.execute(select(AIAnalysis).where(AIAnalysis.test_case_id.in_(w.cases)))).scalars().all()
        versions = (await db.execute(
            select(AIAnalysisVersion.test_case_id, AIAnalysisVersion.version, AIAnalysisVersion.pipeline_run_id,
                   AIAnalysisVersion.analysis_mode, AIAnalysisVersion.llm_model)
            .where(AIAnalysisVersion.test_run_id == w.run)
            .order_by(AIAnalysisVersion.test_case_id, AIAnalysisVersion.version)
        )).all()
        events = (await db.execute(
            select(ProjectActivityEvent.event_type, ProjectActivityEvent.summary, ProjectActivityEvent.context)
            .where(ProjectActivityEvent.project_id == w.project).order_by(ProjectActivityEvent.occurred_at)
        )).all()
        _ = func
    docs = await w.mongo[Collections.AI_RUN_VERSIONS].find(
        {"test_run_id": str(w.run)}, {"_id": 0, "kind": 1, "version": 1, "pipeline_run_id": 1, "meta": 1}
    ).sort([("kind", 1), ("version", 1)]).to_list(length=None)
    summaries = await w.mongo[Collections.RUN_SUMMARIES].count_documents({"test_run_id": str(w.run)})
    return analyses, versions, events, docs, summaries


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_pipeline_twice_keeps_both_answers_and_a_replay_adds_nothing(monkeypatch, gates, live_world):
    from app.db.mongo import Collections

    w = live_world
    p1, p2 = w.pipelines
    # Pipeline 1: the configured model is missing -> the rules engine answers.
    monkeypatch.setattr(gates, "LLM_MODEL", "qwen2.5:7b")
    rules = await _triage_all(monkeypatch, w.cases, _missing_model())
    assert {a["_routing"]["mode_used"] for a in rules.values()} == {"rules"}
    first = await _pipeline(w, p1, rules, "GO")
    analyses_1, *_ = await _counts(w)
    stamped_1 = {a.test_case_id: a.created_at for a in analyses_1}

    # Broker redelivery of pipeline 1: nothing new anywhere.
    replay = await _pipeline(w, p1, rules, "GO")
    assert first.version.created and not replay.version.created and replay.version.version_id == first.version.version_id
    _, versions_r, events_r, docs_r, _ = await _counts(w)
    assert len(versions_r) == 2 and len(docs_r) == 2 and len(events_r) == 1

    # Pipeline 2: a different model, through the real wrapper, answers as an LLM.
    monkeypatch.setattr(gates, "LLM_MODEL", "scripted-v2")
    llm = await _triage_all(monkeypatch, w.cases, _scripted([f"Thought: done\nFinal Answer: {FINAL}"] * 4))
    assert {a["_routing"]["mode_used"] for a in llm.values()} == {"llm"}
    second = await _pipeline(w, p2, llm, "NO_GO")

    analyses, versions, events, docs, summaries = await _counts(w)
    assert len(analyses) == 2, "ai_analysis is still the one-row-per-test latest pointer"
    for a in analyses:  # the set_ fix: the latest row names the new model and time
        assert a.llm_model == "scripted-v2" and a.failure_category == "PRODUCT_BUG"
        assert a.created_at > stamped_1[a.test_case_id]
    assert sorted((v.version, v.pipeline_run_id, v.analysis_mode, v.llm_model) for v in versions) == sorted(
        [(1, p1, "rules", "qwen2.5:7b"), (2, p2, "llm", "scripted-v2")] * 2
    )
    assert [(d["kind"], d["version"], d["pipeline_run_id"]) for d in docs] == [
        ("analysis_digest", 1, str(p1)), ("analysis_digest", 2, str(p2)),
        ("run_summary", 1, str(p1)), ("run_summary", 2, str(p2)),
    ]
    digest_2 = [d for d in docs if d["kind"] == "analysis_digest"][1]
    assert digest_2["meta"]["verdict"] == "NO_GO" and digest_2["meta"]["summary_version"] == 2
    assert summaries == 1, "run_summaries stays the single latest doc"
    info = await w.mongo[Collections.RUN_SUMMARIES].index_information()
    assert info["test_run_id_1"]["unique"] is True
    assert second.verdict_changed is True and second.previous_verdict == "GO"
    # Two rows staged in one transaction can share a timestamp (a coarse
    # clock), so compare as a multiset, not by occurred_at order.
    assert sorted(e.event_type for e in events) == [
        "analysis.completed", "analysis.completed", "analysis.verdict_changed",
    ]
    completed = sorted((e for e in events if e.event_type == "analysis.completed"), key=lambda e: e.context["version"])
    (changed,) = [e for e in events if e.event_type == "analysis.verdict_changed"]
    assert [e.summary for e in completed] == [
        "AI analysis v1 completed for Build 777", "AI analysis v2 completed for Build 777",
    ]
    assert changed.summary == "AI verdict for Build 777 changed GO -> NO_GO"
    assert changed.context == {"from_verdict": "GO", "to_verdict": "NO_GO", "version": 2}
    assert completed[1].context["analysis_mode"] == "llm" and completed[1].context["failing_tests"] == 2
    assert completed[1].context["pipeline_run_id"] == str(p2) and completed[1].context["verdict"] == "NO_GO"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_run_deleted_before_its_summary_is_versioned_leaves_no_orphan(live_world):
    from app.agents.summary_agent import SummaryAgent
    from app.db.mongo import Collections
    from app.models.postgres import TestRun

    w = live_world
    async with w.factory() as db:
        await db.execute(TestRun.__table__.delete().where(TestRun.id == w.run))
        await db.commit()
    await SummaryAgent()._store_summary(str(w.run), _structured(), {"project_id": str(w.project),
                                                                    "pipeline_run_id": str(w.pipelines[0])})
    assert await w.mongo[Collections.AI_RUN_VERSIONS].count_documents({"test_run_id": str(w.run)}) == 0
