from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.decision_report_eval_cycle_service import (
    _aggregate_results,
    assess_report_eval_readiness,
    compute_report_corpus_sha256,
    evaluate_and_persist_report_cycle,
    load_authoritative_report_projections,
    summarize_decision_report_actions,
    summarize_decision_report_feedback,
)


def _report(*, evidence_id: str = "evidence-1") -> dict:
    return {
        "claims": [
            {
                "claim_id": "claim-1",
                "kind": "fact",
                "evidence": [{"evidence_id": evidence_id}],
            }
        ],
        "quality_review": {"contradictions": []},
    }


def test_corpus_hash_is_deterministic_and_redacts_secrets():
    first = compute_report_corpus_sha256(
        "pilot-v1",
        [{**_report(), "secret": "password=hunter2"}],
    )
    second = compute_report_corpus_sha256(
        "pilot-v1",
        [{**_report(), "secret": "password=hunter2"}],
    )
    changed = compute_report_corpus_sha256("pilot-v2", [_report()])
    assert first == second
    assert first != changed
    assert len(first) == 64


def test_aggregate_cycle_fails_empty_and_tracks_worst_check():
    status, metrics, checks, unavailable = _aggregate_results([])
    assert status == "fail"
    assert metrics["reports_evaluated"] == 0
    assert checks[0]["name"] == "corpus"
    assert unavailable == ["corpus"]

    status, _metrics, checks, unavailable = _aggregate_results([
        {
            "metrics": {"citation_validity": 1.0},
            "checks": [{"name": "citation_validity", "status": "pass", "detail": {}}],
            "unavailable_metrics": [],
        },
        {
            "metrics": {"citation_validity": 0.2},
            "checks": [{"name": "citation_validity", "status": "fail", "detail": {}}],
            "unavailable_metrics": ["utility"],
        },
    ])
    assert status == "fail"
    assert checks[0]["status"] == "fail"
    assert unavailable == ["utility"]


@pytest.mark.asyncio
async def test_cycle_is_idempotent_and_tracks_consecutive_passes(monkeypatch):
    monkeypatch.setattr(
        "app.services.decision_report_eval_cycle_service.evaluate_decision_report_quality",
        lambda *_args, **_kwargs: {
            "status": "pass",
            "metrics": {"citation_validity": 1.0},
            "checks": [{"name": "citation_validity", "status": "pass", "detail": {}}],
            "unavailable_metrics": [],
        },
    )
    from app.models.postgres import DecisionReportEvalCycle

    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    existing_result = MagicMock()
    existing_result.scalar_one_or_none.return_value = None
    previous_result = MagicMock()
    corpus_sha = compute_report_corpus_sha256("pilot-v1", [_report()])
    previous = DecisionReportEvalCycle(
        id=uuid.uuid4(),
        cycle_key="previous",
        corpus_version="pilot-v1",
        corpus_sha256=corpus_sha,
        report_count=1,
        status="pass",
        metrics={},
        checks=[],
        unavailable_metrics=[],
        consecutive_passes=1,
    )
    previous_result.scalar_one_or_none.return_value = previous
    db.execute.side_effect = [existing_result, previous_result]
    row, created = await evaluate_and_persist_report_cycle(
        db,
        corpus_version="pilot-v1",
        reports=[_report()],
        cycle_key="cycle-2",
        authorized_evidence_ids=["evidence-1"],
    )
    assert created is True
    assert row.status == "pass"
    assert row.consecutive_passes == 2
    assert row.report_count == 1
    assert len(row.corpus_sha256) == 64
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_cycle_resets_consecutive_passes_when_corpus_hash_changes(monkeypatch):
    monkeypatch.setattr(
        "app.services.decision_report_eval_cycle_service.evaluate_decision_report_quality",
        lambda *_args, **_kwargs: {
            "status": "pass", "metrics": {},
            "checks": [{"name": "citation_validity", "status": "pass"}],
            "unavailable_metrics": [],
        },
    )
    from app.models.postgres import DecisionReportEvalCycle

    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    empty = MagicMock()
    empty.scalar_one_or_none.return_value = None
    previous_result = MagicMock()
    previous_result.scalar_one_or_none.return_value = DecisionReportEvalCycle(
        id=uuid.uuid4(), cycle_key="previous", corpus_version="pilot-v1",
        corpus_sha256="b" * 64, report_count=1, status="pass", metrics={},
        checks=[], unavailable_metrics=[], consecutive_passes=4,
    )
    db.execute.side_effect = [empty, previous_result]
    row, _created = await evaluate_and_persist_report_cycle(
        db, corpus_version="pilot-v1", reports=[_report()], cycle_key="new-cycle",
    )
    assert row.status == "pass"
    assert row.consecutive_passes == 1


def test_readiness_requires_two_passes_and_qualified_user_utility():
    from app.models.postgres import DecisionReportEvalCycle

    latest = DecisionReportEvalCycle(
        id=uuid.uuid4(), cycle_key="cycle-2", corpus_version="pilot-v1",
        corpus_sha256="a" * 64, report_count=5, status="pass",
        metrics={"utility_rate": 0.9}, checks=[], unavailable_metrics=[],
        consecutive_passes=2,
    )
    ready = assess_report_eval_readiness([latest])
    assert ready["status"] == "ready"
    assert ready["reasons"] == []

    low_utility = DecisionReportEvalCycle(
        id=uuid.uuid4(), cycle_key="cycle-3", corpus_version="pilot-v1",
        corpus_sha256="a" * 64, report_count=5, status="pass",
        metrics={"utility_rate": 0.5}, checks=[], unavailable_metrics=[],
        consecutive_passes=2,
    )
    not_ready = assess_report_eval_readiness([low_utility])
    assert not_ready["status"] == "not_ready"
    assert "qualified_user_utility_below_threshold" in not_ready["reasons"]


def test_readiness_fails_closed_without_a_passing_cycle():
    from app.models.postgres import DecisionReportEvalCycle

    row = DecisionReportEvalCycle(
        id=uuid.uuid4(), cycle_key="cycle-1", corpus_version="pilot-v1",
        corpus_sha256="a" * 64, report_count=5, status="warn",
        metrics={}, checks=[], unavailable_metrics=["utility"],
        consecutive_passes=0,
    )
    result = assess_report_eval_readiness([row])
    assert result["status"] == "not_ready"
    assert "latest_cycle_not_pass" in result["reasons"]
    assert "qualified_user_utility_unavailable" in result["reasons"]


@pytest.mark.asyncio
async def test_authoritative_loader_rejects_foreign_runs_and_returns_latest_report():
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    db = MagicMock()
    db.execute = AsyncMock()
    authorized_result = MagicMock()
    authorized_result.scalars.return_value.all.return_value = [run_id]
    db.execute.return_value = authorized_result

    class _Cursor:
        def sort(self, *_args):
            return self

        def limit(self, *_args):
            return self

        async def to_list(self, length):
            assert length > 0
            return [
                {
                    "project_id": str(project_id),
                    "test_run_id": str(run_id),
                    "status": "published",
                    "report_version": 2,
                    "report_id": "report-2",
                    "decision_intelligence": {"claims": []},
                },
                {
                    "project_id": str(project_id),
                    "test_run_id": str(run_id),
                    "status": "published",
                    "report_version": 1,
                    "report_id": "report-1",
                    "decision_intelligence": {"claims": [{"old": True}]},
                },
            ]

    mongo = MagicMock()
    mongo.__getitem__.return_value.find.return_value = _Cursor()
    projections = await load_authoritative_report_projections(
        mongo_db=mongo,
        db=db,
        project_id=project_id,
        test_run_ids=[run_id],
    )
    assert projections == [{
        "claims": [],
        "report_id": "report-2",
        "report_version": 2,
        "verification": {},
    }]

    foreign = MagicMock()
    foreign.scalars.return_value.all.return_value = []
    db.execute.return_value = foreign
    with pytest.raises(ValueError, match="report_eval_run_scope_invalid"):
        await load_authoritative_report_projections(
            mongo_db=mongo,
            db=db,
            project_id=project_id,
            test_run_ids=[run_id],
        )


@pytest.mark.asyncio
async def test_feedback_summary_is_tenant_and_run_scoped():
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    db = MagicMock()
    db.execute = AsyncMock()
    result = MagicMock()
    result.all.return_value = [("useful",), ("partially_useful",), ("not_useful",)]
    db.execute.return_value = result

    summary = await summarize_decision_report_feedback(
        db, project_id=project_id, test_run_ids=[run_id]
    )

    assert summary == {
        "sample_count": 3,
        "useful_count": 1,
        "partially_useful_count": 1,
    }
    statement = db.execute.await_args.args[0]
    assert "decision_report_feedback" in str(statement)
    assert "project_id" in str(statement)
    assert "test_run_id" in str(statement)


@pytest.mark.asyncio
async def test_action_summary_is_tenant_and_run_scoped():
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    db = MagicMock()
    db.execute = AsyncMock()
    result = MagicMock()
    result.all.return_value = [("pending_review",), ("executed",), ("failed",)]
    db.execute.return_value = result

    summary = await summarize_decision_report_actions(
        db, project_id=project_id, test_run_ids=[run_id]
    )

    assert summary["action_count"] == 3
    assert summary["terminal_count"] == 2
    assert summary["unresolved_count"] == 1
    statement = db.execute.await_args.args[0]
    assert "agent_action_ledger" in str(statement)
    assert "project_id" in str(statement)
    assert "test_run_id" in str(statement)
