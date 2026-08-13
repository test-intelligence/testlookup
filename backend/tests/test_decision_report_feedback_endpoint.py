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
