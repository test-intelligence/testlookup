from __future__ import annotations

import uuid
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.postgres import AIEvalBaseline, AIEvalDataset
from app.services import ai_eval_service, eval_gate_service, feedback_service
from app.services.eval_label_provenance import (
    EVAL_LABEL_HOLDOUT_DAYS,
    checksum_from_analysis,
    gate_eligible_items,
)
from app.services.eval_verdict import EvalVerdict
from app.services.review_request_service import _new_request

MANIFEST_M = "a" * 64
MANIFEST_N = "b" * 64
NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


class _Result:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = list(rows or [])
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar


class _DB:
    def __init__(self, *results):
        self._results = iter(results)
        self.statements = []
        self.added = []

    async def execute(self, statement):
        self.statements.append(statement)
        return next(self._results)

    def add(self, row):
        self.added.append(row)


def _feedback(checksum: str, *, age_days: int = 20):
    return SimpleNamespace(
        id=uuid.uuid4(),
        corrected_category=None,
        rating="correct",
        source="manual",
        created_at=NOW - timedelta(days=age_days),
        eval_manifest_checksum=checksum,
    )


def _analysis(checksum: str = MANIFEST_M):
    return SimpleNamespace(
        id=uuid.uuid4(),
        test_case_id=uuid.uuid4(),
        root_cause_summary="Null dereference",
        failure_category="PRODUCT_BUG",
        confidence_score=90,
        is_flaky=False,
        routing_metadata={"analysis_mode": "llm", "eval_manifest_checksum": checksum},
    )


@pytest.mark.asyncio
async def test_dataset_built_from_manifest_m_feedback_cannot_gate_manifest_m():
    feedback = _feedback(MANIFEST_M)
    items = await ai_eval_service.build_dataset_from_feedback(
        _DB(_Result(rows=[(feedback, _analysis())])), as_of=NOW
    )
    assert items[0]["metadata"]["eval_manifest_checksum"] == MANIFEST_M
    assert items[0]["metadata"]["label_source"] == "feedback"

    baseline = AIEvalBaseline(
        task_type="classification",
        agent_name="AnalysisAgent",
        baseline_accuracy=1.0,
        baseline_precision=1.0,
        baseline_recall=1.0,
        baseline_f1=1.0,
        min_accuracy=0.8,
        min_f1=0.75,
        max_regression_pct=5.0,
    )
    dataset = AIEvalDataset(
        id=uuid.uuid4(),
        name="feedback-M",
        task_type="classification",
        items=items,
        item_count=len(items),
    )
    db = _DB(_Result(scalar=baseline), _Result(scalar=dataset))
    result = await eval_gate_service.evaluate_pre_release_gate(
        db,
        task_type="classification",
        agent_name="AnalysisAgent",
        dataset_id=str(dataset.id),
        gate_manifest_checksum=MANIFEST_M,
        evaluated_at=NOW,
    )

    assert result["status"] is EvalVerdict.INSUFFICIENT_SAMPLES
    assert result["current_metrics"] is None


def test_gate_keeps_golden_and_held_out_other_manifest_labels_only():
    old_other = {
        "input": {},
        "expected_output": {},
        "metadata": {
            "feedback_id": "old-other",
            "label_source": "feedback",
            "label_created_at": (NOW - timedelta(days=15)).isoformat(),
            "eval_manifest_checksum": MANIFEST_N,
        },
    }
    same_manifest = {
        **old_other,
        "metadata": {**old_other["metadata"], "feedback_id": "same", "eval_manifest_checksum": MANIFEST_M},
    }
    fresh_other = {
        **old_other,
        "metadata": {
            **old_other["metadata"],
            "feedback_id": "fresh",
            "label_created_at": (NOW - timedelta(days=EVAL_LABEL_HOLDOUT_DAYS - 1)).isoformat(),
        },
    }
    legacy_feedback = {"metadata": {"feedback_id": "legacy"}}
    golden = {"input": {"golden": True}, "expected_output": {}}

    assert gate_eligible_items(
        [same_manifest, fresh_other, legacy_feedback, old_other, golden],
        gate_manifest_checksum=MANIFEST_M,
        evaluated_at=NOW,
    ) == [old_other, golden]


@pytest.mark.asyncio
async def test_feedback_builder_applies_holdout_and_requires_manifest_provenance():
    db = _DB(_Result(rows=[]))
    await ai_eval_service.build_dataset_from_feedback(db, as_of=NOW)
    sql = str(db.statements[0])
    assert "ai_feedback.created_at <=" in sql
    assert "ai_feedback.eval_manifest_checksum IS NOT NULL" in sql


def test_feedback_and_review_rows_copy_the_source_manifest():
    analysis = _analysis(MANIFEST_M)
    assert checksum_from_analysis(analysis) == MANIFEST_M
    assert checksum_from_analysis(SimpleNamespace(routing_metadata={})) is None

    run = SimpleNamespace(
        id=uuid.uuid4(),
        test_run_id=uuid.uuid4(),
        workflow_type="deep",
        execution_metadata={"eval_manifest_checksum": MANIFEST_M},
    )
    review = _new_request(
        project_id=uuid.uuid4(),
        run=run,
        evidence_bundle_sha256=None,
        requested_by=None,
    )
    assert review.eval_manifest_checksum == MANIFEST_M


def test_pipeline_propagates_the_frozen_manifest_to_analysis_persistence():
    from app.agents import analysis_agent, workflow

    pipeline_source = inspect.getsource(workflow.run_offline_pipeline) + inspect.getsource(
        workflow.run_deep_pipeline
    )
    assert pipeline_source.count(
        '"eval_manifest_checksum": str(pipeline_setup.get("eval_manifest_checksum") or "")'
    ) == 2
    analysis_source = inspect.getsource(analysis_agent.AnalysisAgent.run)
    assert 'eval_manifest_checksum=state.get("eval_manifest_checksum")' in analysis_source


@pytest.mark.asyncio
async def test_submit_feedback_stamps_the_analysis_manifest(monkeypatch):
    analysis = _analysis(MANIFEST_M)
    db = _DB(_Result(scalar=analysis))
    monkeypatch.setattr(
        feedback_service, "_require_analysis_access", AsyncMock(return_value=None)
    )
    body = SimpleNamespace(
        rating="correct",
        corrected_category=None,
        corrected_root_cause=None,
        comment=None,
    )

    await feedback_service.submit_feedback(
        db, analysis.id, body, SimpleNamespace(id=uuid.uuid4())
    )

    assert db.added[0].eval_manifest_checksum == MANIFEST_M


@pytest.mark.asyncio
async def test_agent_stack_gate_passes_candidate_manifest_to_every_gate(monkeypatch):
    seen = []

    async def fake_gate(_db, **kwargs):
        seen.append(kwargs)
        return {
            "status": EvalVerdict.PASS,
            "task_type": kwargs["task_type"],
            "agent_name": kwargs["agent_name"],
            "baseline_metrics": {},
        }

    monkeypatch.setattr(eval_gate_service, "evaluate_pre_release_gate", fake_gate)
    result = await eval_gate_service.evaluate_agent_stack_release_gate(
        SimpleNamespace(),
        change_id="E9.10",
        required_gates=[{"task_type": "classification", "agent_name": "AnalysisAgent"}],
        persist=False,
    )

    checksum = result["manifest"]["manifest_checksum_sha256"]
    assert seen[0]["gate_manifest_checksum"] == checksum
    assert seen[0]["evaluated_at"].isoformat() == result["evaluated_at"]
