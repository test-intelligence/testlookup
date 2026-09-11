"""Route-level source authority tests for report evaluation cycles."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.routers.ai_evaluation import (
    ReportEvalCycleRequest,
    create_report_eval_cycle,
    get_report_eval_readiness,
)
from app.models.postgres import DecisionReportEvalCycle


def _admin() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4())


@pytest.fixture(autouse=True)
def scope_checks(monkeypatch):
    """The handler resolves a named project's scope first (code review round 4);
    these tests call it directly with a bare caller, so the check is stubbed."""
    checked = AsyncMock(return_value=(None, None))
    monkeypatch.setattr("app.core.deps.resolve_project_scope", checked)
    return checked


@pytest.mark.asyncio
async def test_a_named_project_is_scope_checked_before_anything_runs(scope_checks):
    from fastapi import HTTPException

    project = uuid.uuid4()
    scope_checks.side_effect = HTTPException(status_code=403, detail="not yours")
    payload = ReportEvalCycleRequest(
        corpus_version="pilot-v1", project_id=project, test_run_ids=[uuid.uuid4()]
    )
    with pytest.raises(HTTPException) as exc:
        await create_report_eval_cycle(payload, _admin(), AsyncMock())
    assert exc.value.status_code == 403
    assert scope_checks.await_args.args[2] == str(project)


@pytest.mark.asyncio
async def test_report_cycle_rejects_caller_corpus_by_default(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.AI_REPORT_EVAL_ALLOW_CALLER_CORPUS", False
    )
    payload = ReportEvalCycleRequest(corpus_version="pilot-v1", reports=[{"claims": []}])
    with pytest.raises(Exception, match="report_eval_authoritative_source_required"):
        await create_report_eval_cycle(payload, _admin(), AsyncMock())


@pytest.mark.asyncio
async def test_report_cycle_requires_a_source(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.AI_REPORT_EVAL_ALLOW_CALLER_CORPUS", True
    )
    payload = ReportEvalCycleRequest(corpus_version="pilot-v1")
    with pytest.raises(Exception, match="report_eval_source_required"):
        await create_report_eval_cycle(payload, _admin(), AsyncMock())


@pytest.mark.asyncio
async def test_authoritative_source_rejects_extra_evidence_ids(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.AI_REPORT_EVAL_ALLOW_CALLER_CORPUS", False
    )
    payload = ReportEvalCycleRequest(
        corpus_version="pilot-v1",
        project_id=uuid.uuid4(),
        test_run_ids=[uuid.uuid4()],
        authorized_evidence_ids=["forged-id"],
    )
    with pytest.raises(Exception, match="report_eval_source_invalid"):
        await create_report_eval_cycle(payload, _admin(), AsyncMock())


@pytest.mark.asyncio
async def test_authoritative_source_passes_tenant_scoped_action_summary(monkeypatch):
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    calls: dict[str, object] = {}

    async def load_reports(**_kwargs):
        return [{"claims": [], "quality_review": {"contradictions": []}}]

    async def feedback(_db, **_kwargs):
        return {"sample_count": 0, "useful_count": 0, "partially_useful_count": 0}

    async def actions(_db, **kwargs):
        calls["action_scope"] = kwargs
        return {"action_count": 1, "terminal_count": 1, "unresolved_count": 0}

    async def persist(_db, **kwargs):
        calls["action_summary"] = kwargs["action_summary"]
        row = SimpleNamespace(
            id=uuid.uuid4(), cycle_key="cycle", corpus_version="pilot-v1",
            corpus_sha256="a" * 64, report_count=1, status="pass",
            metrics={}, checks=[], unavailable_metrics=[], consecutive_passes=1,
            evaluated_by=None, evaluated_at=None,
        )
        return row, True

    monkeypatch.setattr(
        "app.services.decision_report_eval_cycle_service.load_authoritative_report_projections",
        load_reports,
    )
    monkeypatch.setattr(
        "app.services.decision_report_eval_cycle_service.summarize_decision_report_feedback",
        feedback,
    )
    monkeypatch.setattr(
        "app.services.decision_report_eval_cycle_service.summarize_decision_report_actions",
        actions,
    )
    monkeypatch.setattr(
        "app.services.decision_report_eval_cycle_service.evaluate_and_persist_report_cycle",
        persist,
    )

    payload = ReportEvalCycleRequest(
        corpus_version="pilot-v1", project_id=project_id, test_run_ids=[run_id]
    )
    db = AsyncMock()
    response = await create_report_eval_cycle(payload, _admin(), db)

    assert response["status"] == "pass"
    assert calls["action_summary"] == {"action_count": 1, "terminal_count": 1, "unresolved_count": 0}
    assert calls["action_scope"]["project_id"] == project_id
    assert calls["action_scope"]["test_run_ids"] == [run_id]


@pytest.mark.asyncio
async def test_readiness_route_is_fail_closed_until_two_passes_and_utility():
    row = DecisionReportEvalCycle(
        id=uuid.uuid4(), cycle_key="cycle-1", corpus_version="pilot-v1",
        corpus_sha256="a" * 64, report_count=5, status="pass",
        metrics={"utility_rate": 0.9}, checks=[], unavailable_metrics=[],
        consecutive_passes=1,
    )
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row]))
    db = AsyncMock()
    db.execute.return_value = result

    response = await get_report_eval_readiness("pilot-v1", _admin(), db)

    assert response["status"] == "not_ready"
    assert "consecutive_passes_below_threshold" in response["reasons"]
