"""W23c AGT-11 (contract §2.2 I-7 / I-8, D-12): an inline-input invocation's output counts nowhere.

Each exclusion, with fakes:

1. ``run_report_review_filters`` excludes ``subject_type = 'invocation'`` (the run's review for the
   PDF / share / exports / gates / notifications, and the Runs list's verdict);
2. ``create_run_review_request`` for an invocation supersedes nothing, and a stored-run review
   never supersedes an invocation review;
3. Finalize, for an inline plan, stages the review on the INVOCATION, stages no
   ``ai_summary_notifications`` and materialises no action proposal;
4. ``_store_summary`` writes nothing for an inline state (and still writes for a stored one);
5. the "latest pipeline of this run" readers compile ``NOT (EXISTS (… agent_invocations …))``;
6. ``subject_still_exists()`` / ``settle_review`` treat an orphaned invocation review like an
   orphaned pipeline-run review.
"""
from __future__ import annotations

import ast
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models.postgres import ReviewRequest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
SHA = "cd" * 32
NOT_INLINE = (
    "NOT (EXISTS (SELECT 1 FROM agent_invocations WHERE agent_invocations.pipeline_run_id = "
    "agent_pipeline_runs.id AND agent_invocations.input_sha256 IS NOT NULL))"
)


def _sql(stmt, *, literal: bool = False) -> str:
    kwargs = {"compile_kwargs": {"literal_binds": True}} if literal else {}
    return " ".join(str(stmt.compile(dialect=postgresql.dialect(), **kwargs)).split())


class _Capture:
    """A session that records statements and answers every read with nothing."""

    def __init__(self):
        self.statements: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        result.first.return_value = None
        result.scalars.return_value = SimpleNamespace(all=lambda: [], first=lambda: None)
        return result


# ── 1. the run's review never is an invocation's ────────────────────────────────────────────

def test_the_run_report_review_filters_exclude_invocation_reviews():
    from app.services.review_envelope import run_report_review_filters

    for workflow_type in (None, "offline"):
        sql = _sql(select(ReviewRequest.id).where(*run_report_review_filters(workflow_type=workflow_type)), literal=True)
        assert "review_requests.subject_type != 'invocation'" in sql


@pytest.mark.asyncio
async def test_the_runs_list_verdict_read_excludes_invocation_reviews():
    from app.services.review_envelope import report_review_states

    session = _Capture()
    await report_review_states(session, [uuid.uuid4()])
    (stmt,) = session.statements
    assert "review_requests.subject_type != %(subject_type_" in _sql(stmt)


# ── 2. an invocation review supersedes nothing and is superseded by nothing ─────────────────

def _run():
    return SimpleNamespace(id=uuid.uuid4(), test_run_id=uuid.uuid4(), workflow_type="offline", execution_metadata={})


@pytest.mark.asyncio
async def test_an_invocation_review_supersedes_no_pending_run_review():
    from app.services import review_request_service

    pending_run_review = SimpleNamespace(state="pending_review", superseded_by=None)
    session = _Capture()
    db = MagicMock()
    db.flush = AsyncMock()

    async def execute(stmt):
        session.statements.append(stmt)
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value = SimpleNamespace(all=lambda: [pending_run_review])
        return result

    db.execute = execute
    await review_request_service.create_run_review_request(
        db, run=_run(), project_id=uuid.uuid4(), report_stage_names=["summary"],
        evidence_bundle_sha256=SHA, subject_type="invocation", subject_id=str(uuid.uuid4()),
    )
    assert pending_run_review.state == "pending_review" and pending_run_review.superseded_by is None
    assert not any("review_requests.subject_id !=" in _sql(stmt) for stmt in session.statements)


@pytest.mark.asyncio
async def test_a_newer_stored_run_review_never_supersedes_an_invocation_review():
    from app.services import review_request_service

    session = _Capture()
    db = MagicMock()
    db.flush = AsyncMock()
    db.execute = session.execute
    await review_request_service.create_run_review_request(
        db, run=_run(), project_id=uuid.uuid4(), report_stage_names=["summary"], evidence_bundle_sha256=SHA,
    )
    (sweep,) = [stmt for stmt in session.statements if "review_requests.subject_id !=" in _sql(stmt)]
    assert "review_requests.subject_type != " in _sql(sweep)
    assert "invocation" in sweep.compile().params.values()
    # The stored path's own live lookup still names the pipeline_run subject.
    live = [stmt for stmt in session.statements if "review_requests.state != " in _sql(stmt)]
    assert live and all("pipeline_run" in stmt.compile().params.values() for stmt in live)


# ── 3. Finalize: the invocation is the subject; nothing is staged or proposed ───────────────

class _FinalizeSession:
    """Execute order: parent lock, run lock, stages; everything after reads nothing."""

    def __init__(self, pipeline, stages):
        self._results = [pipeline.test_run_id, pipeline, stages]
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, _stmt):
        value = self._results.pop(0) if self._results else None
        result = MagicMock()
        result.scalar_one_or_none.return_value = None if isinstance(value, list) else value
        result.scalars.return_value = SimpleNamespace(all=lambda: list(value) if isinstance(value, list) else [])
        result.first.return_value = None
        return result

    def add(self, _row):
        return None

    async def flush(self):
        return None

    async def commit(self):
        self.committed = True


_SUBJECT = object()


async def _finalize(monkeypatch, *, inline: bool, subject=_SUBJECT):
    from app.agents import workflow
    from app.services import agent_action_ledger_service, review_request_service, run_downstream_outbox

    plan = {"workflow_type": "offline", "invocation_stage": "summary", "stages": []}
    if inline:
        plan["invocation_input_sha256"] = SHA
    project_id, test_run_id, invocation_id = uuid.uuid4(), uuid.uuid4(), str(uuid.uuid4())
    pipeline = SimpleNamespace(
        id=uuid.uuid4(), test_run_id=test_run_id, workflow_type="offline", requested_by=uuid.uuid4(),
        status="running", started_at=datetime.now(timezone.utc), completed_at=None, error=None,
        execution_metadata={"initial_workflow_plan": plan, **({"invocation_input_sha256": SHA} if inline else {})},
        review_policy="human_required", lease_owner="h:1", fencing_token="tok",
        lease_expires_at=datetime.now(timezone.utc),
    )
    stages = [SimpleNamespace(stage_name="ingestion", status="skipped"), SimpleNamespace(stage_name="summary", status="completed")]
    session = _FinalizeSession(pipeline, stages)
    calls = SimpleNamespace(
        review=AsyncMock(return_value=None), proposals=AsyncMock(return_value=0), notify=AsyncMock(return_value=True),
        subject=AsyncMock(return_value=(invocation_id, SHA) if subject is _SUBJECT else subject),
    )
    monkeypatch.setattr(workflow, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(review_request_service, "stage_run_review_request", calls.review)
    monkeypatch.setattr(agent_action_ledger_service, "persist_report_action_proposals", calls.proposals)
    monkeypatch.setattr(run_downstream_outbox, "stage_ai_summary_notification_operation", calls.notify)
    monkeypatch.setattr(workflow.inline_invocation, "inline_review_subject", calls.subject)
    final_state = {
        "project_id": str(project_id), "test_run_id": str(test_run_id), "build_number": "1042",
        "initial_workflow_plan": plan,
        "decision_intelligence": {"proposed_actions": [{"action_id": "a-1", "idempotency_key": "k-1"}]},
        "structured_summary": {"layer1_executive_summary": "Checkout failed.", "_provenance": {}},
    }
    await workflow._mark_pipeline_done(str(pipeline.id), success=True, final_state=final_state)
    return pipeline, session, calls, invocation_id


@pytest.mark.asyncio
async def test_finalize_of_an_inline_pipeline_reviews_the_invocation_and_sends_nothing(monkeypatch):
    pipeline, session, calls, invocation_id = await _finalize(monkeypatch, inline=True)

    assert session.committed and pipeline.status == "completed"
    kwargs = calls.review.await_args.kwargs
    assert kwargs["subject_type"] == "invocation" and kwargs["subject_id"] == invocation_id
    assert kwargs["evidence_bundle_sha256"] == SHA
    calls.notify.assert_not_awaited()
    assert calls.proposals.await_args.kwargs["proposed_actions"] == []
    assert pipeline.execution_metadata["invocation_input_sha256"] == SHA, "kept through the metadata rebuild"


@pytest.mark.asyncio
async def test_finalize_of_a_stored_pipeline_is_unchanged(monkeypatch):
    pipeline, session, calls, _ = await _finalize(monkeypatch, inline=False)

    kwargs = calls.review.await_args.kwargs
    assert "subject_type" not in kwargs and "subject_id" not in kwargs
    calls.subject.assert_not_awaited()
    calls.notify.assert_awaited_once()
    assert calls.proposals.await_args.kwargs["proposed_actions"] == [{"action_id": "a-1", "idempotency_key": "k-1"}]
    assert "invocation_input_sha256" not in pipeline.execution_metadata


@pytest.mark.asyncio
async def test_finalize_never_falls_back_to_a_run_review_when_the_invocation_is_gone(monkeypatch):
    pipeline, session, calls, _ = await _finalize(monkeypatch, inline=True, subject=None)

    # A run report review here would supersede the anchor run's own pending review.
    calls.review.assert_not_awaited()
    calls.notify.assert_not_awaited()
    assert session.committed


# ── 4. no run_summaries write, no AI-history version ───────────────────────────────────────

@pytest.mark.asyncio
async def test_store_summary_writes_nothing_for_an_inline_state_and_still_writes_a_stored_one(monkeypatch):
    from app.agents import summary_agent
    from app.agents.summary_agent import SummaryAgent
    from app.services.inline_invocation import STATE_INPUT_KEY

    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=MagicMock())
    collection.insert_one = AsyncMock(return_value=MagicMock())
    mongo = MagicMock()
    mongo.__getitem__ = MagicMock(return_value=collection)
    get_mongo = MagicMock(return_value=mongo)
    monkeypatch.setattr(summary_agent, "get_mongo_db", get_mongo)
    structured = {
        "layer1_executive_summary": "x", "layer2_incident_view": {}, "layer3_evidence_pack": {},
        "layer4_action_plan": {}, "_provenance": {"fallback_used": False},
    }

    await SummaryAgent()._store_summary("run-1", structured, {"project_id": "p", STATE_INPUT_KEY: SHA})
    get_mongo.assert_not_called()

    await SummaryAgent()._store_summary("run-1", structured, {"project_id": "p"})
    get_mongo.assert_called_once()
    assert collection.update_one.await_count >= 1


@pytest.mark.asyncio
async def test_a_graph_state_is_recognised_by_its_frozen_plan(monkeypatch):
    """The compiled graph keeps only WorkflowState's keys: the seed's own key never reaches a
    node, the frozen plan does (found by the postgres test on the real graph)."""
    from app.agents import summary_agent
    from app.agents.summary_agent import SummaryAgent
    from app.services.inline_invocation import is_inline_state

    get_mongo = MagicMock(side_effect=AssertionError("no run_summaries write"))
    monkeypatch.setattr(summary_agent, "get_mongo_db", get_mongo)
    graph_state = {"project_id": "p", "initial_workflow_plan": {"invocation_input_sha256": SHA, "stages": []}}
    assert is_inline_state(graph_state)
    assert is_inline_state({"workflow_plan": {"invocation_input_sha256": SHA}})
    assert not is_inline_state({"initial_workflow_plan": {"invocation_stage": "summary"}}) and not is_inline_state(None)
    await SummaryAgent()._store_summary("run-1", {"_provenance": {}}, graph_state)
    get_mongo.assert_not_called()


@pytest.mark.asyncio
async def test_the_similar_failure_search_is_skipped_for_an_inline_state(monkeypatch):
    from app.agents.summary_agent import SummaryAgent
    from app.services.inline_invocation import STATE_INPUT_KEY

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    # The search swallows its own errors, so record the calls instead of raising from them.
    opened = MagicMock(side_effect=lambda: _Session())
    recall = AsyncMock(return_value=[{"test_name": "history"}])
    search = AsyncMock(return_value=([{"test_name": "history"}], 1, 1))
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", opened)
    monkeypatch.setattr("app.services.agent_memory_service.recall_similar", recall)
    monkeypatch.setattr("app.services.semantic_search.semantic_search", search)
    analyses = {"inline-01": {"root_cause_summary": "a long enough root cause sentence here", "confidence_score": 90}}
    state = {"project_id": str(uuid.uuid4())}

    out = await SummaryAgent()._fetch_similar_failures(run_data={}, analyses=analyses, state={**state, STATE_INPUT_KEY: SHA})
    assert out == [] and opened.call_count == 0 and recall.await_count == 0 and search.await_count == 0

    await SummaryAgent()._fetch_similar_failures(run_data={}, analyses=analyses, state=state)
    assert opened.call_count == 1 and recall.await_count == 1, "a stored-run summary still searches its history"


# ── 5. the "latest pipeline of this run" readers ─────────────────────────────────────────────

def test_the_predicate_compiles_to_a_correlated_not_exists():
    from app.models.postgres import AgentPipelineRun
    from app.services.inline_invocation import not_inline_pipeline

    sql = _sql(select(AgentPipelineRun.id).where(not_inline_pipeline()))
    assert NOT_INLINE in sql
    assert sql.startswith("SELECT agent_pipeline_runs.id FROM agent_pipeline_runs WHERE"), "correlated, no cross join"


@pytest.mark.asyncio
async def test_the_decision_trail_reads_no_inline_pipeline():
    from app.services.decision_trail_service import _load_pipeline_run

    session = _Capture()
    await _load_pipeline_run(session, uuid.uuid4())
    assert NOT_INLINE in _sql(session.statements[0])


@pytest.mark.asyncio
async def test_the_ai_history_backfill_reads_no_inline_pipeline(monkeypatch):
    from app.services import ai_history_service

    monkeypatch.setattr(ai_history_service, "load_latest_decision_report", AsyncMock(return_value=None))
    session = _Capture()
    await ai_history_service._backfill_pipeline_for(session, MagicMock(), uuid.uuid4())
    assert NOT_INLINE in _sql(session.statements[0])


@pytest.mark.asyncio
async def test_pipeline_admission_neither_blocks_on_nor_counts_an_inline_pipeline():
    from app.services.pipeline_admission import latest_in_progress_pipeline, runs_with_in_progress_pipeline

    session = _Capture()
    await latest_in_progress_pipeline(session, uuid.uuid4(), "offline")
    await runs_with_in_progress_pipeline(session, [uuid.uuid4()], "offline")
    assert len(session.statements) == 2
    assert all(NOT_INLINE in _sql(stmt) for stmt in session.statements)


@pytest.mark.asyncio
async def test_the_run_pipeline_status_reads_no_inline_pipeline():
    from app.routers.agents import get_pipeline_status

    session = _Capture()
    answer = await get_pipeline_status(str(uuid.uuid4()), workflow_type="offline", db=session, _=None)
    assert answer["status"] == "never_run"
    assert NOT_INLINE in _sql(session.statements[0])


def test_both_run_intelligence_pipeline_reads_apply_the_predicate():
    # get_run_intelligence gathers ~20 reads; its two pipeline reads are nested helpers, checked
    # in the source tree (a call inside each helper's where-clause).
    tree = ast.parse((APP_DIR / "services" / "run_intelligence_service.py").read_text(encoding="utf-8"))
    helpers = {
        node.name: node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name in {"_fetch_pipeline_run", "_fetch_deep_pipeline_status"}
    }
    assert set(helpers) == {"_fetch_pipeline_run", "_fetch_deep_pipeline_status"}
    for name, helper in helpers.items():
        calls = {
            getattr(node.func, "id", getattr(node.func, "attr", None))
            for node in ast.walk(helper) if isinstance(node, ast.Call)
        }
        assert "not_inline_pipeline" in calls, name


def test_the_pipelines_list_reads_invocation_reviews_too():
    # NOT excluded on purpose: the pipelines list shows a real run of the agent, so its review
    # summary reads both pipeline-backed subjects (contract §2.2 I-8).
    source = (APP_DIR / "routers" / "agents.py").read_text(encoding="utf-8")
    assert 'ReviewRequest.subject_type.in_(("pipeline_run", "invocation"))' in source


# ── 6. an orphaned invocation review is refused like an orphaned run review ─────────────────

def test_subject_still_exists_covers_invocation_reviews():
    from app.services.review_request_service import subject_still_exists

    sql = _sql(select(ReviewRequest.id).where(subject_still_exists()), literal=True)
    assert "review_requests.subject_type IN ('pipeline_run', 'invocation')" in sql
    assert "review_requests.pipeline_run_id IS NULL" in sql and sql.count("NOT (") == 1


@pytest.mark.asyncio
async def test_settling_an_orphaned_invocation_review_is_refused():
    from app.services.review_request_service import ReviewDecisionRefused, settle_review

    reviewer = SimpleNamespace(id=uuid.uuid4(), is_synthetic=False)
    setattr(reviewer, "_testlookup_credential_kind", "jwt")
    review = SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), subject_type="invocation", subject_id=str(uuid.uuid4()),
        pipeline_run_id=None, test_run_id=uuid.uuid4(), state="pending_review", requested_by=None, kind="report",
        notes=None, reason_code=None, reviewed_by=None, reviewed_at=None,
    )
    with pytest.raises(ReviewDecisionRefused) as exc:
        await settle_review(db=None, review=review, reviewer=reviewer, decision="accepted")
    assert (exc.value.status_code, exc.value.code) == (409, "subject_run_deleted")
    assert review.state == "pending_review" and review.reviewed_by is None
