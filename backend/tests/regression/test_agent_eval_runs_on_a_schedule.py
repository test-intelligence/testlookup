"""Regression guard: the agent stack is evaluated on a schedule (F-11).

The finding
-----------
Nothing evaluated on a schedule. The beat schedule ran twelve jobs — coverage,
purge, training export, finetune checks, reindex, digests, probes — and **not
one** of them measured output quality. ``eval_gate_service`` was reachable only
from an ADMIN API route or the prompt-attestation CLI, so quality was gated when
a prompt changed and never otherwise. A model swap, a routing change, or slow
drift between prompt edits was invisible.

The blocker underneath
----------------------
Scheduling the gate alone would have made it worse. ``ai_eval_datasets`` was
**empty on the deployment** (0 datasets, 0 baselines), because seeding existed
only behind an API route nobody calls. With no dataset the gate returns
``FAIL: no evaluation dataset found`` — a permanent red light that means
"nothing was measured", not "quality regressed". Shipping that would be a metric
that lies, which is the failure mode this whole effort exists to remove.

So the task seeds first, idempotently, and only then evaluates.

What is guarded
---------------
* the schedule actually contains an evaluation job, and points at a real task;
* seeding is idempotent — a second run creates nothing;
* seeding has ONE implementation, shared by the route and the task;
* a scheduled run works with no user (``created_by`` is nullable);
* ``insufficient_samples`` stays blocking: a gate with nothing to compare against has
  not passed.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("asyncpg")

from app.services import eval_gate_service  # noqa: E402
from app.services.eval_gate_service import (  # noqa: E402
    _BLOCKING_GATE_STATUSES,
    DEFAULT_AGENT_STACK_GATES,
    GateStatus,
    ensure_golden_datasets,
)
from app.services.golden_datasets import get_all_golden_datasets  # noqa: E402
from app.worker.celery_app import celery_app  # noqa: E402

# Celery registers tasks on module import; without this the registry holds
# only the built-ins and the 'is it registered' assertion is vacuous.
import app.worker.tasks as worker_tasks  # noqa: E402,F401


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    """Records what would be inserted, and can pretend rows already exist."""

    def __init__(self, existing_names: set[str] | None = None):
        self.existing = existing_names or set()
        self.added: list = []
        self.flushed = False

    async def execute(self, stmt):
        # Return a row for any dataset whose name we were told already exists.
        text = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        for name in self.existing:
            if name in text:
                return _FakeResult(object())
        return _FakeResult(None)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushed = True


# ── The schedule contains an evaluation ──────────────────────────────────────


def test_the_beat_schedule_has_an_evaluation_job():
    """The finding: twelve scheduled jobs, none of them about quality."""
    schedule = celery_app.conf.beat_schedule
    eval_jobs = {
        name: cfg for name, cfg in schedule.items()
        if "eval" in name or "eval" in str(cfg.get("task", ""))
    }

    assert eval_jobs, "no scheduled job evaluates the agent stack"


def test_the_scheduled_job_points_at_a_real_task():
    """A beat entry naming a task that does not exist fails silently at runtime."""
    schedule = celery_app.conf.beat_schedule
    entry = schedule["daily-agent-eval"]

    assert entry["task"] == "app.worker.tasks.run_scheduled_agent_eval"
    assert entry["task"] in celery_app.tasks, (
        "the scheduled task is not registered — beat would fire into nothing"
    )


def test_it_does_not_collide_with_the_other_nightly_jobs():
    """02:00 is the purge, 03:00 the finetune check."""
    schedule = celery_app.conf.beat_schedule
    cron = schedule["daily-agent-eval"]["schedule"]

    assert 4 in cron.hour


# ── Seeding: idempotent, shared, and user-free ───────────────────────────────


@pytest.mark.asyncio
async def test_seeding_creates_the_golden_datasets_when_absent():
    db = _FakeSession()

    created = await ensure_golden_datasets(db)

    assert len(created) == len(get_all_golden_datasets())
    assert len(db.added) == len(created)
    assert db.flushed


@pytest.mark.asyncio
async def test_seeding_is_idempotent():
    """The task runs daily; the cost after the first run must be four SELECTs."""
    names = {spec["name"] for spec in get_all_golden_datasets()}
    db = _FakeSession(existing_names=names)

    created = await ensure_golden_datasets(db)

    assert created == []
    assert db.added == []


@pytest.mark.asyncio
async def test_a_scheduled_run_needs_no_user():
    """created_by is nullable precisely so beat can seed."""
    db = _FakeSession()

    await ensure_golden_datasets(db, created_by=None)

    assert all(row.created_by is None for row in db.added)


def test_seeding_has_one_implementation():
    """A second copy is how the route and the schedule drift apart."""
    from app.routers import ai_evaluation

    src = inspect.getsource(ai_evaluation.seed_golden_datasets)
    assert "ensure_golden_datasets" in src, "the route must delegate, not re-implement"
    assert "AIEvalDataset(" not in src, "the route is re-building datasets itself"


# ── Absence is not health ────────────────────────────────────────────────────


def test_no_baseline_is_still_blocking():
    """A gate with nothing to compare against has not passed."""
    assert GateStatus.INSUFFICIENT_SAMPLES in _BLOCKING_GATE_STATUSES
    assert GateStatus.PASS not in _BLOCKING_GATE_STATUSES


def test_every_required_gate_has_a_golden_dataset():
    """A gate whose dataset never seeds can only ever report FAIL."""
    seeded_types = {spec["task_type"] for spec in get_all_golden_datasets()}
    required = {g["task_type"] for g in DEFAULT_AGENT_STACK_GATES}

    assert required <= seeded_types, (
        f"gates with no golden dataset: {sorted(required - seeded_types)}"
    )


def test_the_task_seeds_before_it_evaluates():
    """Evaluating first would report FAIL-no-dataset on a fresh deployment."""
    from app.worker import tasks

    src = inspect.getsource(tasks.run_scheduled_agent_eval)
    assert src.index("ensure_golden_datasets") < src.index(
        "evaluate_agent_stack_release_gate("
    ), "the gate would run against an empty dataset on first execution"


def test_the_service_exposes_seeding_for_the_scheduler():
    assert callable(eval_gate_service.ensure_golden_datasets)


@pytest.mark.asyncio
async def test_nightly_producer_writes_one_eval_run_per_task_type():
    from types import SimpleNamespace

    class _RunSession:
        def __init__(self):
            self.added = []
            self.flushed = False

        async def execute(self, _stmt):
            return _FakeResult(SimpleNamespace(id=__import__("uuid").uuid4()))

        def add(self, row):
            self.added.append(row)

        async def flush(self):
            self.flushed = True

    db = _RunSession()
    results = [
        {"task_type": "classification", "current_metrics": {"total": 5, "correct": 4, "accuracy": 0.8}},
        {"task_type": "root_cause", "current_metrics": {"total": 3, "correct": 3, "accuracy": 1.0}},
    ]
    rows = await eval_gate_service.persist_nightly_eval_runs(
        db, gate_results=results, model_name="fixture-model",
    )
    assert [row.task_type for row in rows] == ["classification", "root_cause"]
    assert all(row.model_name == "fixture-model" for row in rows)
    assert db.added == rows
    assert db.flushed


def test_never_baselined_is_reported_distinctly_from_regressed():
    """Verified against the deployment: with zero baselines all four gates
    return insufficient_samples, so the daily job reports missing evidence —
    and a permanently-red job is one nobody reads. The gate's own status keeps
    blocking releases; only the scheduled signal separates the two cases."""
    from app.worker import tasks

    src = inspect.getsource(tasks.run_scheduled_agent_eval)
    assert 'EvalVerdict.INSUFFICIENT_SAMPLES.value' in src
    assert '"signal": signal' in src
    # The release semantics must NOT be softened to achieve it.
    assert 'EvalVerdict.INSUFFICIENT_SAMPLES' in src
    assert '"status": gate_status' in src, "the gate's own status must survive intact"
