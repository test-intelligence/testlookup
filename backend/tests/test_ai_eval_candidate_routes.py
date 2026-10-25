"""W16 AGT-09: the ai_evaluation router's candidate-evaluation routes over scripted sessions.

``POST /runs/evaluate/{dataset_id}``: no body is the legacy evaluation, byte for
byte; a ``candidate`` body runs every check before anything is written (nothing
added, no commit), then ONE commit holds the run and its activity row and the
task is dispatched AFTER it. The AI config is read on the REQUEST session.
``GET /runs`` answers ``{items, total}``; ``GET /runs/{id}/items`` is ADMIN only
and re-sanitizes what it returns. A real FastAPI app drives the router so the
status codes, the optional body and the role gates are the real ones.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import AIEvalDataset, AIEvalItemOutput, AIEvalRun
from app.routers import ai_evaluation as router

PROJECT = uuid.UUID("0a1b2c3d-4e5f-4a6b-8c7d-9e0fa1b2c3d4")
EVALUATED = datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._rows[0]

    def scalars(self):
        return self


class Session:
    """Answers each ``execute`` with the next scripted result; records every write."""

    def __init__(self, *results, calls: list | None = None):
        self._results = list(results)
        self.statements: list = []
        self.added: list = []
        self.calls = calls if calls is not None else []

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        return self._results.pop(0) if self._results else _Rows([])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.calls.append("flush")

    async def commit(self):
        self.calls.append("commit")

    async def rollback(self):
        self.calls.append("rollback")

    async def refresh(self, obj):
        self.calls.append("refresh")
        if getattr(obj, "id", None) is None:
            obj.id = uuid.UUID("5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b")
        if getattr(obj, "evaluated_at", None) is None:
            obj.evaluated_at = EVALUATED
        for column in obj.__table__.columns:  # what the INSERT's Python defaults would have stored
            default = column.default
            if getattr(obj, column.key, None) is None and default is not None and default.is_scalar:
                setattr(obj, column.key, default.arg)


def _user(role: str = "ADMIN"):
    return SimpleNamespace(id=uuid.UUID("1f2e3d4c-5b6a-4978-8a9b-0c1d2e3f4a5b"), role=role,
                           is_active=True, api_key_project_id=None, username=f"u-{role.lower()}")


def _dataset(*, task_type="classification", items=None) -> AIEvalDataset:
    items = items if items is not None else [
        {"input": {"failure_category": "PRODUCT_BUG", "confidence_score": 90, "test_name": "tA"},
         "expected_output": {"failure_category": "PRODUCT_BUG", "correct": True}},
        {"input": {"failure_category": "FLAKY", "confidence_score": 70, "test_name": "tB"},
         "expected_output": {"failure_category": "PRODUCT_BUG", "correct": False}},
        {"input": {"failure_category": "FLAKY", "confidence_score": 80, "test_name": "tC"},
         "expected_output": {"failure_category": "FLAKY", "correct": True}},
    ]
    return AIEvalDataset(id=uuid.UUID("2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c6d"), name="Golden: Failure Classification",
                         task_type=task_type, items=items, item_count=len(items))


@pytest.fixture
def client():
    """``(make, state)``: ``make(session)`` -> an AsyncClient over the router, signed in as ``state["user"]``."""
    app = FastAPI()
    app.include_router(router.router)
    state = {"user": _user("ADMIN")}
    app.dependency_overrides[get_current_active_user] = lambda: state["user"]

    def make(session: Session) -> AsyncClient:
        async def _db():
            yield session

        app.dependency_overrides[get_db] = _db
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    return make, state


@pytest.fixture
def activity(monkeypatch):
    recorded = AsyncMock()
    monkeypatch.setattr(router, "record_activity", recorded)
    return recorded


def _evaluate_path(dataset) -> str:
    return f"/api/v1/ai-eval/runs/evaluate/{dataset.id}"


def _sql(statement) -> str:
    from sqlalchemy.dialects import postgresql

    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


# ── the legacy path (no body) ────────────────────────────────────────────────


async def test_no_body_is_the_legacy_evaluation_byte_for_byte(client, activity, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_MODEL", "golden-model")
    make, _ = client
    dataset = _dataset()
    session = Session(_Rows([dataset]))
    async with make(session) as http:
        resp = await http.post(_evaluate_path(dataset))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert isinstance(body["duration_ms"], int)
    assert body == {
        "id": "5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b", "dataset_id": str(dataset.id),
        "model_name": "golden-model", "model_version_id": None, "task_type": "classification",
        "precision": 0.75, "recall": 0.75, "f1_score": 0.75, "accuracy": 0.6667,
        "agreement_rate": 2 / 3, "total_items": 3, "correct_items": 2, "fallback_used": False,
        "evaluated_at": "2026-10-20T09:30:00Z", "duration_ms": body["duration_ms"],
        # W16: the new fields at their defaults -- a legacy run is labelled, never recomputed
        "metric_kind": "label_agreement", "metric_label": "Legacy label-agreement metric",
        "status": "completed", "error_code": None, "error_text": None, "candidate": None,
        "project_id": None, "requested_by": None, "started_at": None, "completed_at": None,
        "heartbeat_at": None, "scored_items": 0, "unscored_items": 0, "error_items": 0,
        "items_capped": False, "metrics": None, "measured": True,
    }
    assert [type(o).__name__ for o in session.added] == ["AIEvalRun"]
    assert session.calls == ["commit", "refresh"]
    activity.assert_not_awaited()  # the legacy path records nothing new


async def test_a_qa_lead_cannot_evaluate(client, activity):
    make, state = client
    state["user"] = _user("QA_LEAD")
    session = Session()
    async with make(session) as http:
        resp = await http.post(_evaluate_path(_dataset()), json={
            "candidate": {"tier": "rules"}, "project_id": str(PROJECT)})
    assert resp.status_code == 403 and "Requires at least ADMIN" in resp.text
    assert session.statements == [] and session.added == []


async def test_a_missing_dataset_is_the_unchanged_404(client, activity):
    make, _ = client
    async with make(Session(_Rows([]))) as http:
        resp = await http.post(f"/api/v1/ai-eval/runs/evaluate/{uuid.uuid4()}", json={
            "candidate": {"tier": "rules"}, "project_id": str(PROJECT)})
    assert resp.status_code == 404 and resp.json() == {"detail": "Dataset not found"}


# ── the candidate path: every refusal writes nothing ─────────────────────────


async def test_the_body_project_is_checked_before_anything_else(client, activity, monkeypatch):
    refused = AsyncMock(side_effect=HTTPException(403, "You do not have access to this project"))
    monkeypatch.setattr(router, "_authorize_body_project", refused)
    make, state = client
    session = Session(_Rows([_dataset()]))
    async with make(session) as http:
        resp = await http.post(_evaluate_path(_dataset()), json={
            "candidate": {"tier": "rules"}, "project_id": str(PROJECT)})
    assert resp.status_code == 403
    assert refused.await_args.args[1:] == (state["user"], PROJECT)
    assert len(session.statements) == 1 and session.added == [] and session.calls == []
    activity.assert_not_awaited()


@pytest.mark.parametrize("task_type, tier, code, message", [
    ("classification", "gpu", "candidate_tier_invalid", "tier must be rules, ml or llm."),
    ("duplicate_detection", "ml", "candidate_tier_unsupported", "duplicate_detection replays the rules tier only."),
    ("release_decision", "llm", "candidate_tier_unsupported", "release_decision replays the rules tier only."),
])
async def test_a_tier_outside_the_vocabulary_or_the_task_is_refused(client, activity, task_type, tier, code, message):
    make, _ = client
    session = Session(_Rows([_dataset(task_type=task_type)]))
    async with make(session) as http:
        resp = await http.post(_evaluate_path(_dataset()), json={
            "candidate": {"tier": tier}, "project_id": str(PROJECT)})
    assert resp.status_code == 422
    assert resp.json() == {"detail": {"code": code, "message": message}}
    assert session.added == [] and session.calls == []


def _resolved(tier: str = "rules", **kw):
    from app.models.schemas import EvalCandidateIn, EvalCandidateResolved

    return EvalCandidateResolved(tier=tier, offline_mode=False, requested=EvalCandidateIn(tier=tier), **kw)


@pytest.fixture
def resolver(monkeypatch):
    """The REAL runner module with ``resolve_candidate`` recorded (waiting for G-C until it lands)."""
    from app.services import ai_eval_runner

    seen: dict = {}

    async def _resolve(db, *, task_type, candidate, project_id):
        seen.update(db=db, task_type=task_type, candidate=candidate, project_id=project_id)
        if seen.get("refuse"):
            raise ai_eval_runner.CandidateRefused(*seen["refuse"])
        return _resolved(candidate.tier)

    monkeypatch.setattr(ai_eval_runner, "resolve_candidate", _resolve)
    return seen


async def test_a_refused_candidate_answers_its_code_on_the_request_session(client, activity, resolver):
    sentence = ("Offline mode is on and openai is not a local provider: only rules, ML or a local model "
                "can be evaluated.")
    resolver["refuse"] = ("candidate_offline_blocked", sentence)
    make, _ = client
    session = Session(_Rows([_dataset()]))
    async with make(session) as http:
        resp = await http.post(_evaluate_path(_dataset()), json={
            "candidate": {"tier": "llm"}, "project_id": str(PROJECT)})
    assert resp.status_code == 422
    assert resp.json()["detail"] == {"code": "candidate_offline_blocked", "message": sentence}
    # the AI config is read on THIS request's session, never a second one
    assert resolver["db"] is session
    assert (resolver["task_type"], resolver["project_id"]) == ("classification", PROJECT)
    assert session.added == [] and session.calls == []


async def test_the_real_resolver_reads_the_ai_config_through_the_request_session(client, activity, monkeypatch):
    """No patch of the runner: ``get_effective_ai_config(db=<the request session>)`` decides."""
    from app.services import ai_config_resolver

    seen = {}

    async def _config(*args, **kwargs):
        seen.update(args=args, kwargs=kwargs)
        return {"provider": "openai", "model": "gpt-x", "offline_mode": True}

    monkeypatch.setattr(ai_config_resolver, "get_effective_ai_config", _config)
    make, _ = client
    session = Session(_Rows([_dataset()]))
    async with make(session) as http:
        resp = await http.post(_evaluate_path(_dataset()), json={
            "candidate": {"tier": "llm"}, "project_id": str(PROJECT)})
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "candidate_offline_blocked"
    assert seen["kwargs"].get("db") is session and seen["args"] == ()
    assert session.added == [] and session.calls == []


async def test_a_second_run_of_the_same_dataset_and_tier_is_409(client, activity, resolver):
    make, _ = client
    session = Session(_Rows([_dataset()]), _Rows([uuid.uuid4()]))
    async with make(session) as http:
        resp = await http.post(_evaluate_path(_dataset()), json={
            "candidate": {"tier": "rules"}, "project_id": str(PROJECT)})
    assert resp.status_code == 409
    assert resp.json()["detail"] == {
        "code": "candidate_run_in_progress", "message": "A rules evaluation of this dataset is already running.",
    }
    in_flight = _sql(session.statements[1])
    assert "'queued', 'running'" in in_flight and "metric_kind = 'replay'" in in_flight
    assert "(ai_eval_runs.candidate ->> 'tier') = 'rules'" in in_flight
    assert session.added == [] and session.calls == []


async def test_an_empty_dataset_is_refused(client, activity, resolver):
    make, _ = client
    session = Session(_Rows([_dataset(items=[])]), _Rows([]))
    async with make(session) as http:
        resp = await http.post(_evaluate_path(_dataset()), json={
            "candidate": {"tier": "rules"}, "project_id": str(PROJECT)})
    assert resp.status_code == 422
    assert resp.json()["detail"] == {"code": "dataset_empty", "message": "The dataset has no items to replay."}
    assert session.added == [] and session.calls == []


@pytest.fixture
def dispatch(monkeypatch):
    """The REAL task's ``apply_async`` recorded; its kwargs are BOUND to the task's signature."""
    from app.worker import tasks

    state: dict = {"sent": [], "calls": []}

    def _apply_async(*args, **kwargs):
        inspect.signature(tasks.run_candidate_evaluation.run).bind(**kwargs["kwargs"])
        state["calls"].append("dispatch")
        state["sent"].append(kwargs)

    monkeypatch.setattr(tasks.run_candidate_evaluation, "apply_async", _apply_async)
    return state


async def test_a_candidate_is_queued_in_one_commit_and_dispatched_after_it(client, activity, resolver, dispatch):
    calls: list = dispatch["calls"]
    activity.side_effect = lambda *a, **k: calls.append("activity")
    make, state = client
    dataset = _dataset()
    session = Session(_Rows([dataset]), _Rows([]), calls=calls)
    async with make(session) as http:
        resp = await http.post(_evaluate_path(dataset), json={
            "candidate": {"tier": "rules"}, "project_id": str(PROJECT), "limit": 2})
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    assert resp.json() == {"run_id": run_id, "status": "queued"}

    (run,) = session.added
    assert isinstance(run, AIEvalRun) and str(run.id) == run_id
    assert (run.metric_kind, run.status, run.project_id, run.requested_by) == (
        "replay", "queued", PROJECT, state["user"].id)
    assert (run.total_items, run.items_capped, run.task_type, run.model_name) == (2, True, "classification", "rules")
    assert run.candidate == _resolved("rules").model_dump(mode="json")
    # ONE commit holds the run and its activity row; the task goes AFTER it
    assert calls == ["activity", "commit", "dispatch"]
    assert dispatch["sent"] == [{"kwargs": {"run_id": run_id}, "queue": "ai_analysis"}]
    kwargs = activity.await_args.kwargs
    assert activity.await_args.args[0] is session
    assert kwargs["event_type"] == "ai_eval.candidate_requested"
    assert (kwargs["project_id"], kwargs["entity_id"], kwargs["entity_label"]) == (
        PROJECT, PROJECT, "Golden: Failure Classification")
    assert kwargs["context"] == {"run_id": run_id, "dataset_id": str(dataset.id), "task_type": "classification",
                                 "tier": "rules", "item_count": 2}


async def test_a_broker_error_after_the_commit_is_never_a_500(client, activity, resolver, monkeypatch):
    from app.worker import tasks

    def _down(*args, **kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.run_candidate_evaluation, "apply_async", _down)
    make, _ = client
    session = Session(_Rows([_dataset()]), _Rows([]))
    async with make(session) as http:
        resp = await http.post(_evaluate_path(_dataset()), json={
            "candidate": {"tier": "rules"}, "project_id": str(PROJECT)})
    assert resp.status_code == 202 and resp.json()["status"] == "queued"
    assert session.calls == ["commit"]  # the queued row stays for the reconcile sweep
    (run,) = session.added
    assert (run.total_items, run.items_capped) == (3, False)


async def test_candidate_options_are_read_on_the_request_session(client, monkeypatch):
    from app.models.schemas import CandidateOptionsResponse
    from app.services import ai_eval_runner

    seen = {}

    async def _options(db):
        seen["db"] = db
        return CandidateOptionsResponse(tiers=[], prompt_version=None, offline_mode=True, max_items=200,
                                        supported_tiers_by_task={"classification": ["rules", "ml", "llm"]})

    monkeypatch.setattr(ai_eval_runner, "candidate_options", _options)
    make, state = client
    session = Session()
    async with make(session) as http:
        resp = await http.get("/api/v1/ai-eval/candidate-options")
        state["user"] = _user("QA_LEAD")
        refused = await http.get("/api/v1/ai-eval/candidate-options")
    assert resp.status_code == 200 and resp.json()["max_items"] == 200
    assert seen["db"] is session and session.added == [] and session.calls == []
    assert refused.status_code == 403


# ── the reads ────────────────────────────────────────────────────────────────


def _stored_run(*, kind="replay", status="completed") -> AIEvalRun:
    return AIEvalRun(
        id=uuid.UUID("3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f"), dataset_id=_dataset().id, model_name="rules",
        task_type="classification", metric_kind=kind, status=status, accuracy=0.5, f1_score=0.5,
        total_items=3, correct_items=1, scored_items=2, unscored_items=1, error_items=0,
        items_capped=False, fallback_used=False, evaluated_at=EVALUATED,
    )


async def test_list_runs_answers_items_and_total_and_judges_its_filters(client):
    make, state = client
    state["user"] = _user("QA_LEAD")
    session = Session(_Rows([_stored_run()]), _Rows([7]))
    async with make(session) as http:
        resp = await http.get("/api/v1/ai-eval/runs", params={"metric_kind": "replay", "status": "completed"})
        bad_kind = await http.get("/api/v1/ai-eval/runs", params={"metric_kind": "heuristic"})
        bad_status = await http.get("/api/v1/ai-eval/runs", params={"status": "done"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 7 and [r["metric_kind"] for r in body["items"]] == ["replay"]
    assert body["items"][0]["metric_label"] == "Replay of the candidate"
    page, count = (_sql(s) for s in session.statements)
    for sql in (page, count):  # the total counts the SAME filtered set
        assert "metric_kind = 'replay'" in sql and "status = 'completed'" in sql
    assert bad_kind.json()["detail"] == {
        "code": "metric_kind_invalid", "message": "metric_kind must be label_agreement or replay."}
    assert bad_status.json()["detail"] == {
        "code": "status_invalid", "message": "status must be queued, running, completed, partial or failed."}
    assert (bad_kind.status_code, bad_status.status_code) == (422, 422)
    assert session.added == [] and session.calls == []


async def test_one_run_is_readable_by_a_qa_lead_and_a_missing_one_is_404(client):
    make, state = client
    state["user"] = _user("QA_LEAD")
    session = Session(_Rows([_stored_run()]), _Rows([]))
    async with make(session) as http:
        found = await http.get(f"/api/v1/ai-eval/runs/{_stored_run().id}")
        missing = await http.get(f"/api/v1/ai-eval/runs/{uuid.uuid4()}")
    assert found.status_code == 200 and found.json()["scored_items"] == 2
    assert missing.status_code == 404
    assert missing.json()["detail"] == {"code": "eval_run_not_found", "message": "Evaluation run not found."}


def _item(index: int, status: str, **kw) -> AIEvalItemOutput:
    return AIEvalItemOutput(
        id=uuid.uuid4(), eval_run_id=_stored_run().id, item_index=index, item_key=f"k{index}", status=status,
        error_code=kw.get("error_code"), expected=kw.get("expected", {"failure_category": "PRODUCT_BUG"}),
        output=kw.get("output"), score=kw.get("score", {}), latency_ms=12, tokens=None, mode_used="rules",
    )


async def test_items_are_admin_only(client):
    make, state = client
    state["user"] = _user("QA_LEAD")
    session = Session()
    async with make(session) as http:
        resp = await http.get(f"/api/v1/ai-eval/runs/{_stored_run().id}/items")
    assert resp.status_code == 403 and "Requires at least ADMIN" in resp.text
    assert session.statements == []


async def test_items_re_sanitize_what_they_return(client):
    """A stored output, expected value or dataset input that carries a secret comes back redacted."""
    make, _ = client
    leaky = "auth failed with password=hunter2Secret"
    dataset = _dataset(items=[{"input": {"error_message": leaky}, "expected_output": {}}] * 3)
    rows = [
        _item(0, "incorrect", output={"root_cause_summary": leaky, "failure_category": "FLAKY"}),
        _item(1, "error", error_code="item_timeout", score={"seconds": 45}),
    ]
    session = Session(
        _Rows([_stored_run()]),
        _Rows([("incorrect", 1), ("error", 1), ("correct", 4)]),
        _Rows(rows),
        _Rows([dataset.items]),
    )
    async with make(session) as http:
        resp = await http.get(f"/api/v1/ai-eval/runs/{_stored_run().id}/items", params={"limit": 25})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"] == {"incorrect": 1, "error": 1, "correct": 4} and body["total"] == 6
    first, second = body["items"]
    assert first["output"]["root_cause_summary"] == "auth failed with password=[REDACTED]"
    assert first["input"] == {"error_message": "auth failed with password=[REDACTED]"}
    assert "hunter2Secret" not in resp.text
    assert second["error_text"] == "The engine did not answer within 45 s."
    assert session.added == [] and session.calls == []


async def test_items_filter_by_status_inside_the_run_and_judge_it(client):
    make, _ = client
    session = Session(_Rows([_stored_run()]), _Rows([("incorrect", 2), ("correct", 9)]), _Rows([]))
    async with make(session) as http:
        resp = await http.get(f"/api/v1/ai-eval/runs/{_stored_run().id}/items",
                              params={"status": "incorrect", "offset": 50})
        bad = await http.get(f"/api/v1/ai-eval/runs/{_stored_run().id}/items", params={"status": "wrong"})
    assert resp.json() == {"items": [], "total": 2, "counts": {"incorrect": 2, "correct": 9}}
    page = _sql(session.statements[2])
    assert "ai_eval_item_outputs.status = 'incorrect'" in page and f"eval_run_id = '{_stored_run().id}'" in page
    assert len(session.statements) == 3  # no dataset read for an empty page
    assert bad.status_code == 422 and bad.json()["detail"]["message"] == (
        "status must be correct, incorrect, unscored or error.")


async def test_the_gate_and_the_baseline_answer_a_refused_run_with_code_and_sentence(client):
    make, _ = client
    partial = _stored_run(status="partial")
    session = Session(_Rows([]), _Rows([partial]))
    async with make(session) as http:
        gate = await http.post("/api/v1/ai-eval/pre-release-gate", json={
            "task_type": "classification", "agent_name": "AnalysisAgent", "candidate_run_id": str(uuid.uuid4())})
        baseline = await http.post("/api/v1/ai-eval/baselines", json={
            "task_type": "classification", "agent_name": "AnalysisAgent", "eval_run_id": str(partial.id)})
    assert gate.status_code == 404
    assert gate.json()["detail"] == {"code": "candidate_run_not_found", "message": "Candidate run not found."}
    assert baseline.status_code == 409
    assert baseline.json()["detail"] == {
        "code": "candidate_run_not_complete",
        "message": "Only a completed replay run can be gated; this one is partial.",
    }
    assert session.added == []


async def test_a_legacy_baseline_body_keeps_its_404_string(client, monkeypatch):
    from app.services import eval_gate_service

    monkeypatch.setattr(eval_gate_service, "_load_dataset_items", AsyncMock(return_value=[]))
    make, _ = client
    async with make(Session()) as http:
        resp = await http.post("/api/v1/ai-eval/baselines", json={"task_type": "root_cause"})
    assert resp.status_code == 404 and resp.json() == {"detail": "No dataset found for task_type=root_cause"}


async def test_baseline_rows_carry_their_metric_kind_run_and_dataset(client):
    from app.models.postgres import AIEvalBaseline

    run_id = uuid.uuid4()
    row = AIEvalBaseline(id=uuid.uuid4(), task_type="classification", agent_name="AnalysisAgent",
                         prompt_version="replay-rules", metric_kind="replay", eval_run_id=run_id,
                         dataset_id=_dataset().id, is_active=True, created_at=EVALUATED)
    legacy = AIEvalBaseline(id=uuid.uuid4(), task_type="root_cause", agent_name="AnalysisAgent",
                            prompt_version="v1", is_active=True, created_at=EVALUATED)
    make, state = client
    state["user"] = _user("QA_LEAD")
    async with make(Session(_Rows([row, legacy]))) as http:
        resp = await http.get("/api/v1/ai-eval/baselines")
    first, second = resp.json()
    assert (first["metric_kind"], first["eval_run_id"], first["dataset_id"]) == (
        "replay", str(run_id), str(_dataset().id))
    assert (second["metric_kind"], second["eval_run_id"], second["dataset_id"]) == ("label_agreement", None, None)


async def test_a_legacy_run_stored_no_items(client):
    make, _ = client
    session = Session(_Rows([_stored_run(kind="label_agreement")]))
    async with make(session) as http:
        resp = await http.get(f"/api/v1/ai-eval/runs/{_stored_run().id}/items")
    assert resp.json() == {"items": [], "total": 0, "counts": {}}
    assert len(session.statements) == 1
