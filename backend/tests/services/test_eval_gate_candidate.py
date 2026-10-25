"""W16 AGT-09: the pre-release gate and the baseline over a stored REPLAY run.

``evaluate_pre_release_gate(..., candidate_run_id=)`` scores the run's STORED
metrics against the active baseline with the existing ``_evaluate_rules``; a
baseline of another kind or dataset is never compared (``insufficient_samples``
with the reason). ``set_baseline_from_eval(..., eval_run_id=)`` keeps one replay
baseline per tier. Without the new keyword both answer as before, plus the new
keys. Scripted sessions: the gate reads the run, then the baseline.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.models.postgres import AIEvalBaseline, AIEvalRun
from app.services import eval_gate_service as svc
from app.services.eval_verdict import EvalVerdict


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, *rows):
        self._rows = list(rows)

    def scalars(self):
        return _Scalars(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _DB:
    """Answers each ``execute`` with the next scripted result; records adds."""

    def __init__(self, *results):
        self._results = list(results)
        self.statements: list = []
        self.added: list = []
        self.flush = AsyncMock()
        self.commit = AsyncMock()

    async def execute(self, statement):
        self.statements.append(statement)
        return self._results.pop(0) if self._results else _Result()

    def add(self, obj):
        self.added.append(obj)


DATASET = uuid.uuid4()


def _run(*, accuracy=0.9, f1=0.88, scored=10, status="completed", kind="replay",
         task_type="classification", tier="rules", dataset=DATASET) -> AIEvalRun:
    return AIEvalRun(
        id=uuid.uuid4(), dataset_id=dataset, model_name=tier, task_type=task_type,
        metric_kind=kind, status=status, accuracy=accuracy, precision=accuracy, recall=accuracy,
        f1_score=f1, scored_items=scored, correct_items=round(accuracy * scored), unscored_items=1,
        error_items=0, candidate={"tier": tier, "model": None, "offline_mode": False,
                                  "requested": {"tier": tier}},
    )


def _baseline(*, accuracy=0.9, kind="replay", dataset=DATASET, run_id=None) -> AIEvalBaseline:
    return AIEvalBaseline(
        task_type="classification", agent_name="AnalysisAgent", prompt_version="replay-rules",
        baseline_accuracy=accuracy, baseline_precision=accuracy, baseline_recall=accuracy,
        baseline_f1=accuracy, min_accuracy=0.8, min_f1=0.75, max_regression_pct=5.0,
        metric_kind=kind, dataset_id=dataset, eval_run_id=run_id, is_active=True,
    )


async def _gate(db, run, **kw):
    return await svc.evaluate_pre_release_gate(
        db, task_type=kw.pop("task_type", "classification"), agent_name="AnalysisAgent",
        candidate_run_id=run.id if run is not None else uuid.uuid4(), **kw,
    )


@pytest.mark.asyncio
async def test_a_replay_run_against_a_replay_baseline_passes_on_its_stored_scores():
    run = _run(accuracy=0.9)
    base_run = uuid.uuid4()
    db = _DB(_Result(run), _Result(_baseline(accuracy=0.9, run_id=base_run)))
    result = await _gate(db, run)
    assert result["status"] is EvalVerdict.PASS
    assert result["metric_kind"] == "replay"
    assert result["candidate_run_id"] == str(run.id)
    assert result["baseline_run_id"] == str(base_run)
    assert result["dataset_id"] == str(DATASET)
    # total is the SCORED count, never the dataset size or an unscored item
    assert result["current_metrics"]["total"] == 10
    assert result["current_metrics"]["accuracy"] == 0.9


@pytest.mark.asyncio
async def test_a_worse_replay_fails_the_no_regression_rule():
    run = _run(accuracy=0.8, f1=0.8)
    db = _DB(_Result(run), _Result(_baseline(accuracy=0.95)))
    result = await _gate(db, run)
    assert result["status"] is EvalVerdict.FAIL
    failed = {r["rule"] for r in result["rule_results"] if not r["passed"]}
    assert "no_regression" in failed


@pytest.mark.asyncio
async def test_a_legacy_baseline_is_never_compared_with_a_replay():
    run = _run(accuracy=0.5, f1=0.5)  # would FAIL a numeric comparison
    db = _DB(_Result(run), _Result(_baseline(accuracy=0.95, kind="label_agreement")))
    result = await _gate(db, run)
    assert result["status"] is EvalVerdict.INSUFFICIENT_SAMPLES
    first = result["rule_results"][0]
    assert first["rule"] == "baseline_metric_kind" and first["passed"] is False
    assert first["detail"] == (
        "The active baseline uses the legacy label-agreement metric; set a replay run as the baseline first."
    )
    assert "no_regression" not in {r["rule"] for r in result["rule_results"]}
    assert result["baseline_metrics"]["metric_kind"] == "label_agreement"


@pytest.mark.asyncio
async def test_a_baseline_on_another_dataset_is_not_evidence():
    run = _run()
    db = _DB(_Result(run), _Result(_baseline(dataset=uuid.uuid4())))
    result = await _gate(db, run)
    assert result["status"] is EvalVerdict.INSUFFICIENT_SAMPLES
    assert result["rule_results"][0] == {
        "rule": "baseline_dataset", "passed": False, "detail": "The baseline was measured on another dataset.",
    }


@pytest.mark.asyncio
async def test_no_baseline_is_insufficient_not_pass():
    run = _run()
    result = await _gate(_DB(_Result(run), _Result()), run)
    assert result["status"] is EvalVerdict.INSUFFICIENT_SAMPLES
    assert result["baseline_run_id"] is None


@pytest.mark.parametrize(
    "run_kw, status, code, message",
    [
        ({"status": "partial"}, 409, "candidate_run_not_complete",
         "Only a completed replay run can be gated; this one is partial."),
        ({"status": "running"}, 409, "candidate_run_not_complete",
         "Only a completed replay run can be gated; this one is running."),
        ({"kind": "label_agreement"}, 409, "candidate_run_not_complete",
         "Only a completed replay run can be gated; this one is a legacy label-agreement run."),
        ({"task_type": "root_cause"}, 422, "candidate_run_task_mismatch",
         "The candidate run evaluated root_cause, not classification."),
    ],
)
@pytest.mark.asyncio
async def test_a_run_that_cannot_be_gated_is_refused_with_its_sentence(run_kw, status, code, message):
    run = _run(**run_kw)
    db = _DB(_Result(run), _Result(_baseline()))
    with pytest.raises(svc.CandidateRunRefused) as exc:
        await _gate(db, run)
    assert (exc.value.status_code, exc.value.code, exc.value.message) == (status, code, message)
    assert len(db.statements) == 1  # refused before the baseline is read


@pytest.mark.asyncio
async def test_a_missing_run_is_404():
    with pytest.raises(svc.CandidateRunRefused) as exc:
        await _gate(_DB(_Result()), None)
    assert (exc.value.status_code, exc.value.code) == (404, "candidate_run_not_found")
    assert exc.value.message == "Candidate run not found."
    assert not isinstance(exc.value, ValueError)  # the baseline route's 404-string path stays legacy


@pytest.mark.asyncio
async def test_without_a_candidate_run_the_gate_answers_as_before_plus_the_new_keys(monkeypatch):
    """The legacy path: the same statements, rules and metrics; four keys added."""
    items = [{"input": {}, "expected_output": {"correct": True}}] * 4
    monkeypatch.setattr(svc, "_load_dataset_items", AsyncMock(return_value=items))
    legacy = _baseline(kind="label_agreement", dataset=None)
    db = _DB(_Result(legacy))
    result = await svc.evaluate_pre_release_gate(db, task_type="classification", agent_name="AnalysisAgent")
    expected_metrics = svc.compute_metrics_for_task_type("classification", items)
    assert result["current_metrics"] == expected_metrics
    assert result["rule_results"] == svc._evaluate_rules(expected_metrics, legacy)
    assert set(result) == {
        "status", "task_type", "agent_name", "current_metrics", "baseline_metrics", "rule_results",
        "evaluated_at", "metric_kind", "candidate_run_id", "baseline_run_id", "dataset_id",
    }
    assert (result["metric_kind"], result["candidate_run_id"], result["baseline_run_id"]) == (
        "label_agreement", None, None,
    )
    assert "metric_kind" not in result["baseline_metrics"]  # the legacy dict is unchanged
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_set_baseline_from_a_replay_run_keeps_one_replay_baseline_per_tier():
    rules_run = _run(accuracy=0.84, tier="rules")
    legacy = AIEvalBaseline(task_type="classification", agent_name="AnalysisAgent", prompt_version="v1",
                            is_active=True, metric_kind="label_agreement")
    old_rules = AIEvalBaseline(task_type="classification", agent_name="AnalysisAgent",
                               prompt_version="replay-rules", is_active=False, metric_kind="replay")
    db = _DB(_Result(rules_run), _Result(legacy, old_rules))
    result = await svc.set_baseline_from_eval(
        db, task_type="classification", agent_name="AnalysisAgent", eval_run_id=rules_run.id,
    )
    # the replay-rules row is refreshed IN PLACE (unique key), the legacy one deactivated
    assert db.added == [legacy]
    assert legacy.is_active is False
    assert old_rules.is_active is True
    assert (old_rules.metric_kind, old_rules.eval_run_id, old_rules.dataset_id) == (
        "replay", rules_run.id, DATASET,
    )
    assert old_rules.baseline_accuracy == 0.84
    assert result["metric_kind"] == "replay" and result["eval_run_id"] == str(rules_run.id)
    assert result["metrics"]["total"] == 10
    db.commit.assert_not_awaited()

    # an ML run is a SECOND replay key: a new row, the rules one deactivated
    ml_run = _run(accuracy=0.9, tier="ml")
    db2 = _DB(_Result(ml_run), _Result(legacy, old_rules))
    await svc.set_baseline_from_eval(
        db2, task_type="classification", agent_name="AnalysisAgent", eval_run_id=ml_run.id,
    )
    new_rows = [r for r in db2.added if getattr(r, "prompt_version", None) == "replay-ml"]
    assert len(new_rows) == 1 and new_rows[0].is_active is True
    assert old_rules.is_active is False


@pytest.mark.asyncio
async def test_set_baseline_refuses_a_partial_run_before_touching_a_baseline():
    db = _DB(_Result(_run(status="partial")))
    with pytest.raises(svc.CandidateRunRefused) as exc:
        await svc.set_baseline_from_eval(
            db, task_type="classification", agent_name="AnalysisAgent", eval_run_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 409
    assert db.added == [] and len(db.statements) == 1


@pytest.mark.asyncio
async def test_a_legacy_rebaseline_resets_the_kind_of_a_row_it_refreshes(monkeypatch):
    monkeypatch.setattr(svc, "_load_dataset_items", AsyncMock(return_value=[{"x": 1}]))
    row = AIEvalBaseline(task_type="classification", agent_name="AnalysisAgent", prompt_version="replay-rules",
                         is_active=True, metric_kind="replay", eval_run_id=uuid.uuid4())
    result = await svc.set_baseline_from_eval(
        _DB(_Result(row)), task_type="classification", agent_name="AnalysisAgent", prompt_version="replay-rules",
    )
    assert (row.metric_kind, row.eval_run_id) == ("label_agreement", None)
    assert (result["metric_kind"], result["eval_run_id"]) == ("label_agreement", None)


@pytest.mark.asyncio
async def test_the_agent_stack_history_excludes_project_tier_comparisons():
    """§0.4: the history read selected EVERY gate row, incl. another project's G2 verdicts."""
    from app.routers import ai_evaluation as router

    class _Count:
        def scalar_one(self):
            return 0

    db = _DB(_Result(), _Count())
    answer = await router.list_agent_stack_gate_runs(
        change_id=None, status_filter=None, limit=20, current_user=object(), db=db,
    )
    assert answer.items == [] and answer.total == 0
    assert len(db.statements) == 2
    for statement in db.statements:  # the page AND its count
        assert "ai_eval_gate_runs.gate_type IS NULL" in str(statement)


@pytest.mark.asyncio
async def test_drift_averages_the_legacy_label_agreement_series_only():
    """A candidate replay is compared by the gate, never averaged into drift (both windows)."""
    from types import SimpleNamespace

    from sqlalchemy.dialects import postgresql

    from app.services.ai_eval_service import detect_quality_drift

    window = SimpleNamespace(avg_accuracy=0.9, avg_f1=0.9, avg_agreement=0.9, run_count=2,
                             correct_items=18, total_items=20)

    class _One:
        def one_or_none(self):
            return window

    class _Drift(_DB):
        async def execute(self, statement):
            self.statements.append(statement)
            return _One()

    db = _Drift()
    await detect_quality_drift(db, task_type="classification", window_days=7)
    assert len(db.statements) == 2
    for statement in db.statements:
        sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "ai_eval_runs.metric_kind = 'label_agreement'" in sql
