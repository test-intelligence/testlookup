from __future__ import annotations

from unittest.mock import AsyncMock
import uuid

import pytest

from app.routers import feedback as feedback_router


@pytest.mark.asyncio
async def test_report_feedback_endpoint_delegates_and_commits(monkeypatch):
    expected = {
        "feedback_id": "feedback-1",
        "status": "recorded",
        "report_id": "report-1",
        "report_version": 2,
    }
    service = AsyncMock(return_value=expected)
    mongo = object()
    monkeypatch.setattr(feedback_router.feedback_service, "submit_decision_report_feedback", service)
    monkeypatch.setattr(feedback_router, "get_mongo_db", lambda: mongo)
    db = type("DB", (), {"commit": AsyncMock()})()
    body = feedback_router.DecisionReportFeedbackRequest(
        report_version=2,
        feedback_kind="utility",
        utility_rating="useful",
    )
    user = type("User", (), {"id": uuid.uuid4()})()

    result = await feedback_router.submit_decision_report_feedback(
        uuid.uuid4(), "report-1", body, db, user,
    )

    assert result == expected
    service.assert_awaited_once()
    db.commit.assert_awaited_once()
    assert service.await_args.args[1] is mongo


def test_report_feedback_route_is_run_guarded():
    route = next(
        route for route in feedback_router.router.routes
        if getattr(route, "path", "") == "/api/v1/runs/{run_id}/decision-reports/{report_id}/feedback"
    )
    assert "POST" in route.methods
    dependency_callables = [dependency.call for dependency in route.dependant.dependencies]
    assert any(getattr(callable_obj, "__name__", "") == "_check" for callable_obj in dependency_callables)


def _claim_correction_body():
    return feedback_router.DecisionReportFeedbackRequest(
        report_version=2,
        feedback_kind="claim_correction",
        claim_id="claim-1",
        correction_type="category",
        corrected_value="PRODUCT_BUG",
        reason="The log shows a product defect.",
        evidence_ids=["artifact-1"],
    )


class _RunProjectDB:
    """Answers the run's project lookup; records commits."""

    def __init__(self, project_id):
        self.project_id = project_id
        self.commit = AsyncMock()

    async def execute(self, _stmt):
        project_id = self.project_id
        return type("R", (), {"scalar_one_or_none": lambda _self: project_id})()


@pytest.mark.asyncio
async def test_a_claim_correction_needs_qa_engineer_in_the_run_project(monkeypatch):
    """Batch 4 review: a VIEWER member could file claim corrections, which the
    report evaluation cycle reads. Same floor as every other correction."""
    service = AsyncMock()
    monkeypatch.setattr(feedback_router.feedback_service, "submit_decision_report_feedback", service)
    monkeypatch.setattr(feedback_router, "get_mongo_db", object)
    asked = []

    async def holds(_db, _user, project_id, min_role):
        asked.append((project_id, min_role))
        return False

    monkeypatch.setattr(feedback_router, "holds_role_for_project_write", holds)
    project_id = uuid.uuid4()
    db = _RunProjectDB(project_id)
    user = type("User", (), {"id": uuid.uuid4(), "role": "VIEWER"})()

    with pytest.raises(feedback_router.HTTPException) as refused:
        await feedback_router.submit_decision_report_feedback(
            uuid.uuid4(), "report-1", _claim_correction_body(), db, user,
        )

    assert refused.value.status_code == 403
    assert asked == [(project_id, feedback_router.UserRole.QA_ENGINEER)]
    service.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_qa_engineer_files_a_claim_correction(monkeypatch):
    service = AsyncMock(return_value={"status": "recorded"})
    monkeypatch.setattr(feedback_router.feedback_service, "submit_decision_report_feedback", service)
    monkeypatch.setattr(feedback_router, "get_mongo_db", object)

    async def holds(*_args):
        return True

    monkeypatch.setattr(feedback_router, "holds_role_for_project_write", holds)
    db = _RunProjectDB(uuid.uuid4())
    user = type("User", (), {"id": uuid.uuid4(), "role": "QA_ENGINEER"})()

    result = await feedback_router.submit_decision_report_feedback(
        uuid.uuid4(), "report-1", _claim_correction_body(), db, user,
    )

    assert result == {"status": "recorded"}
    service.assert_awaited_once()
