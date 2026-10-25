"""W16 AGT-09 (b): the ``run_candidate_evaluation`` task and the reconcile sweep.

The task body is driven in-process through ``task.run`` / ``task.apply`` (the
REAL Celery signature, never a mocked ``apply_async``); the runner's row
functions are replaced by recorders over scripted sessions, while
``replay_item`` is the real one (the rules engine, or an exhausted LLM budget
that calls nothing). The SQL itself runs against Postgres in
``tests/integration/test_ai_eval_runner_postgres.py``. Every time is built
inside the test.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql

from app.models.schemas import EvalCandidateIn, EvalCandidateResolved
from app.services import ai_eval_runner as runner
from app.services.golden_datasets import GOLDEN_DATASETS
from app.services.pipeline_ai_scope import build_ai_config_snapshot
from app.services.step_llm_budget import StepLLMBudget
from app.worker import tasks
from app.worker.celery_app import celery_app

RUN = uuid.UUID("0000aaaa-0000-0000-0000-00000000c0de")
PROJECT = uuid.UUID("00000000-0000-0000-0000-0000000000a1")


class _Session:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        self.log.append("open")
        return self

    async def __aexit__(self, *exc):
        self.log.append("close")
        return False

    async def commit(self):
        self.log.append("commit")


def _plan(*, tier="rules", n=4, stored=(), budget=None) -> runner.RunPlan:
    items = GOLDEN_DATASETS["classification"]["get_items"]()[:n]
    return runner.RunPlan(
        run_id=RUN, task_type="classification",
        candidate=EvalCandidateResolved(tier=tier, offline_mode=False, requested=EvalCandidateIn(tier=tier)),
        project_id=PROJECT, items=list(enumerate(items)), stored=set(stored),
        budget=budget or StepLLMBudget(limit=50), timeout_s=30.0, ai_scope=build_ai_config_snapshot({}),
    )


def _patch(monkeypatch, *, plan, claimed=True, settled_at=None):
    """Recorders for the runner's row functions; ``replay_item`` stays real."""
    log: list = []

    async def claim_run(db, *, run_id, now):
        assert run_id == RUN and now.tzinfo is not None
        log.append("claim")
        return claimed

    async def load_plan(db, *, run_id):
        log.append("plan")
        if isinstance(plan, Exception):
            raise plan
        return plan

    async def stage_item_output(db, *, run, index, item, outcome, now=None):
        if settled_at is not None and index == settled_at:
            raise runner.RunSettled(str(run))
        log.append(("stage", index, outcome.status, outcome.error_code))
        return True

    async def stage_budget_exhausted(db, *, run, items, now=None):
        log.append(("exhausted", [index for index, _ in items]))
        return len(items)

    async def finish_run(db, *, run, now, task_type=None):
        log.append("finish")
        staged = [e for e in log if isinstance(e, tuple)]
        return "partial" if any(e[0] == "exhausted" for e in staged) else "completed"

    async def fail_run(db, *, run, now, error_code, task_type=None):
        log.append(("fail", error_code))
        return "failed"

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", lambda: _Session(log), raising=False)
    for name, fn in (("claim_run", claim_run), ("load_plan", load_plan), ("stage_item_output", stage_item_output),
                     ("stage_budget_exhausted", stage_budget_exhausted), ("finish_run", finish_run),
                     ("fail_run", fail_run)):
        monkeypatch.setattr(runner, name, fn)
    return log


def test_the_dispatch_payload_binds_to_the_task_signature():
    """The route dispatches ``apply_async(kwargs={"run_id": ...}, queue="ai_analysis")``."""
    inspect.signature(tasks.run_candidate_evaluation.run).bind(run_id=str(RUN))
    assert tasks.run_candidate_evaluation.queue == "ai_analysis"
    assert tasks.run_candidate_evaluation.max_retries == 0
    assert tasks.run_candidate_evaluation.name in celery_app.tasks


def test_each_item_is_committed_on_its_own_session(monkeypatch):
    log = _patch(monkeypatch, plan=_plan(n=3))

    outcome = tasks.run_candidate_evaluation.run(run_id=str(RUN))

    assert outcome == {"status": "completed", "run_id": str(RUN), "replayed": 3}
    assert log == [
        "open", "claim", "plan", "commit", "close",
        "open", ("stage", 0, "incorrect", None), "commit", "close",
        "open", ("stage", 1, "incorrect", None), "commit", "close",
        "open", ("stage", 2, "incorrect", None), "commit", "close",
        "open", "finish", "commit", "close",
    ]


def test_a_run_that_is_not_queued_replays_nothing(monkeypatch):
    log = _patch(monkeypatch, plan=_plan(), claimed=False)

    assert tasks.run_candidate_evaluation.run(run_id=str(RUN)) == {"skipped": "not_queued", "run_id": str(RUN)}
    assert log == ["open", "claim", "close"]


def test_resume_skips_the_items_already_stored(monkeypatch):
    log = _patch(monkeypatch, plan=_plan(n=4, stored={0, 2}))

    outcome = tasks.run_candidate_evaluation.run(run_id=str(RUN))

    assert outcome["replayed"] == 2
    assert [e[1] for e in log if isinstance(e, tuple)] == [1, 3]


def test_an_exhausted_budget_stages_the_rest_and_ends_partial(monkeypatch):
    log = _patch(monkeypatch, plan=_plan(tier="llm", n=4, budget=StepLLMBudget(limit=0)))

    outcome = tasks.run_candidate_evaluation.run(run_id=str(RUN))

    assert outcome == {"status": "partial", "run_id": str(RUN), "replayed": 1}
    staged = [e for e in log if isinstance(e, tuple)]
    assert staged == [("stage", 0, "error", "budget_exhausted"), ("exhausted", [1, 2, 3])]
    assert log.count("commit") == 3  # claim, the exhausted batch (one session), finish


def test_a_missing_dataset_fails_the_run_without_replaying(monkeypatch):
    log = _patch(monkeypatch, plan="dataset_missing")

    outcome = tasks.run_candidate_evaluation.run(run_id=str(RUN))

    assert outcome == {"status": "failed", "error_code": "dataset_missing", "run_id": str(RUN)}
    assert log == ["open", "claim", "plan", ("fail", "dataset_missing"), "commit", "close"]


def test_a_run_reconciled_mid_flight_stops_writing(monkeypatch):
    log = _patch(monkeypatch, plan=_plan(n=3), settled_at=1)

    outcome = tasks.run_candidate_evaluation.run(run_id=str(RUN))

    assert outcome == {"stopped": "settled", "run_id": str(RUN), "replayed": 1}
    assert "finish" not in log


def test_a_run_level_exception_fails_the_run_with_the_modules_handler_shape(monkeypatch):
    from structlog.testing import capture_logs

    log = _patch(monkeypatch, plan=ConnectionRefusedError(111, "Connect call failed"))
    with capture_logs() as captured:
        exc = tasks.run_candidate_evaluation.apply(kwargs={"run_id": str(RUN)}).result

    assert isinstance(exc, RuntimeError)
    assert str(exc) == "ConnectionRefusedError: candidate evaluation failed"
    assert exc.__suppress_context__ is True  # ``from None`` ran: the original trace stays private
    assert ("fail", "run_error") in log
    errors = [e for e in captured if e["event"] == "candidate_evaluation_failed"]
    assert errors and errors[0]["error_type"] == "ConnectionRefusedError" and errors[0]["log_level"] == "error"


# ── the runner's statements over a scripted session ─────────────────────────


class _Result:
    def __init__(self, *, rows=(), scalar=None, rowcount=0):
        self._rows, self._scalar, self.rowcount = list(rows), scalar, rowcount

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self


class _Scripted:
    """Answers each ``execute`` with the next scripted result; keeps the SQL."""

    def __init__(self, results):
        self.results, self.sql = list(results), []

    async def execute(self, statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        self.sql.append((str(compiled), compiled.params))
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_the_claim_moves_only_a_queued_replay():
    now = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)
    db = _Scripted([_Result(rowcount=1), _Result(rowcount=0)])

    assert await runner.claim_run(db, run_id=RUN, now=now) is True
    assert await runner.claim_run(db, run_id=RUN, now=now) is False
    sql, params = db.sql[0]
    assert sql.startswith("UPDATE ai_eval_runs SET status=%(status)s, started_at=%(started_at)s")
    assert params["status"] == "running" and params["started_at"] == now
    assert {"status_1": "queued", "metric_kind_1": "replay"}.items() <= params.items()


@pytest.mark.asyncio
async def test_staging_bumps_a_running_run_then_inserts_idempotently():
    now = datetime(2026, 10, 25, 9, 1, tzinfo=timezone.utc)
    item = {"input": {"error_message": "boom"}, "expected_output": {"failure_category": "FLAKY"},
            "metadata": {"label": "fpLabelA"}}
    outcome = runner.ItemOutcome(status="correct", output={"failure_category": "FLAKY"}, score={"expected": "FLAKY"})
    db = _Scripted([_Result(scalar=RUN), _Result(scalar=None)])

    inserted = await runner.stage_item_output(db, run=RUN, index=4, item=item, outcome=outcome, now=now)

    assert inserted is False  # ON CONFLICT DO NOTHING returned no row: already stored
    update_sql, update_params = db.sql[0]
    assert update_sql.startswith("UPDATE ai_eval_runs SET ")
    assert "correct_items=(ai_eval_runs.correct_items + %(correct_items_1)s)" in update_sql
    assert "WHERE ai_eval_runs.id = %(id_1)s::UUID AND ai_eval_runs.status = %(status_1)s" in update_sql
    assert update_params["status_1"] == "running" and update_params["heartbeat_at"] == now
    insert_sql, insert_params = db.sql[1]
    assert "ON CONFLICT (eval_run_id, item_index) DO NOTHING" in insert_sql
    assert insert_params["item_key"] == "fpLabelA" and insert_params["item_index"] == 4

    settled = _Scripted([_Result(scalar=None)])
    with pytest.raises(runner.RunSettled):
        await runner.stage_item_output(settled, run=RUN, index=5, item=item, outcome=outcome, now=now)
    assert len(settled.sql) == 1  # nothing inserted for a reconciled / deleted run


@pytest.mark.asyncio
async def test_a_stored_output_is_redacted():
    item = {"input": {}, "expected_output": {"failure_category": "FLAKY"}}
    secret = "Authorization: Bearer fpTokenAbcdefghijklmnopqrstuvwx0123456789"
    outcome = runner.ItemOutcome(status="incorrect", output={"root_cause_summary": secret})
    db = _Scripted([_Result(scalar=RUN), _Result(scalar=uuid.uuid4())])

    assert await runner.stage_item_output(db, run=RUN, index=0, item=item, outcome=outcome) is True
    stored = db.sql[1][1]["output"]
    assert "fpTokenAbcdefghijklmnopqrstuvwx0123456789" not in str(stored)


@pytest.mark.asyncio
async def test_reconcile_settles_silent_runs_from_a_now_built_here():
    now = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)
    running, queued = uuid.uuid4(), uuid.uuid4()
    db = _Scripted([
        _Result(rows=[(running, "classification", "running"), (queued, "classification", "queued")]),
        _Result(rows=[("correct", {"expected": "FLAKY", "predicted": "FLAKY"}, None),
                      ("incorrect", {"expected": "FLAKY", "predicted": "PRODUCT_BUG"}, None)]),
        _Result(scalar=running),
        _Result(rows=[]),
        _Result(scalar=queued),
    ])

    assert await runner.reconcile_stale_runs(db, now=now) == 2

    select_sql, select_params = db.sql[0]
    assert "FOR UPDATE SKIP LOCKED" in select_sql
    assert now - timedelta(seconds=900) in select_params.values()
    partial = db.sql[2][1]
    assert (partial["status"], partial["error_code"], partial["scored_items"]) == ("partial", "worker_lost", 2)
    assert partial["accuracy"] == 0.5 and partial["completed_at"] == now
    failed = db.sql[4][1]
    assert (failed["status"], failed["error_code"], failed["scored_items"]) == ("failed", "worker_lost", 0)
    assert failed["accuracy"] is None  # nothing scored: not measured, never 0 %


def test_the_reconcile_beat_entry_runs_every_ten_minutes():
    entry = celery_app.conf.beat_schedule["reconcile-stale-eval-runs"]
    assert entry["task"] == "app.worker.tasks.reconcile_stale_eval_runs"
    assert entry["task"] in celery_app.tasks
    assert entry["schedule"].minute == set(range(0, 60, 10))
    assert entry["options"]["queue"] == "default"
    inspect.signature(tasks.reconcile_stale_eval_runs.run).bind(*entry.get("args", ()), **entry.get("kwargs", {}))


def test_the_sweep_commits_each_batch_and_stops_below_a_full_one(monkeypatch):
    log: list = []
    counts = [100, 100, 7]

    async def reconcile(db, *, now, limit):
        log.append(("batch", now, limit))
        return counts.pop(0)

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", lambda: _Session(log), raising=False)
    monkeypatch.setattr(runner, "reconcile_stale_runs", reconcile)

    assert tasks.reconcile_stale_eval_runs.run() == {"settled": 207, "batches": 3}
    assert log.count("commit") == 3
    assert len({e[1] for e in log if isinstance(e, tuple)}) == 1  # one ``now`` for the sweep
