from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routers import ai_evaluation as router
from app.services import reviewer_quality_service as svc


@pytest.fixture(autouse=True)
def body_project_check(monkeypatch):
    """Membership of the body's project is stubbed for direct handler calls."""
    check = AsyncMock()
    monkeypatch.setattr(router, "_authorize_body_project", check)
    return check


def _body(**changes) -> router.ReviewerQualityRequest:
    values = {
        "project_id": uuid.uuid4(),
        "agent_id": svc.REVIEWER_AGENT_ID,
        "persist": True,
        "auto_disable": False,
    }
    values.update(changes)
    return router.ReviewerQualityRequest(**values)


@pytest.mark.asyncio
async def test_route_scores_persists_audits_and_commits(monkeypatch) -> None:
    row = SimpleNamespace(id=uuid.uuid4())
    persist = AsyncMock(return_value=row)
    monkeypatch.setattr(svc, "persist_reviewer_quality", persist)
    activity = AsyncMock()
    monkeypatch.setattr(router, "record_activity", activity)
    db = SimpleNamespace(commit=AsyncMock())
    user = SimpleNamespace(id=uuid.uuid4())

    result = await router.run_reviewer_quality(
        _body(),
        current_user=user,
        db=db,
    )

    assert result["verdict"] == "insufficient_samples"
    assert result["reviewer_quality_id"] == str(row.id)
    persist.assert_awaited_once()
    assert activity.await_args.kwargs["event_type"] == "ai_eval.reviewer_evaluated"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_route_refuses_non_reviewer_agent() -> None:
    with pytest.raises(HTTPException) as exc:
        await router.run_reviewer_quality(
            _body(agent_id="agent.summary.v1"),
            current_user=SimpleNamespace(id=uuid.uuid4()),
            db=SimpleNamespace(),
        )
    assert exc.value.status_code == 422
    assert "agent.reviewer.v1 only" in exc.value.detail


@pytest.mark.asyncio
async def test_auto_disable_requires_durable_evidence() -> None:
    with pytest.raises(HTTPException) as exc:
        await router.run_reviewer_quality(
            _body(persist=False, auto_disable=True),
            current_user=SimpleNamespace(id=uuid.uuid4()),
            db=SimpleNamespace(),
        )
    assert exc.value.status_code == 422
    assert "persist=true" in exc.value.detail


@pytest.mark.asyncio
async def test_route_is_project_scoped_and_requires_qa_lead(body_project_check) -> None:
    """The project is in the BODY: the old path-reading guards never ran, and
    ``require_project_role`` fell back to the GLOBAL role, so a QA lead of
    project A switched off project B's second-model check (E2E 2026-10-10).
    The handler now checks the body's project before anything is computed."""
    body_project_check.side_effect = HTTPException(403, "You do not have access to this project")
    body = _body(auto_disable=True)
    user = SimpleNamespace(id=uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        await router.run_reviewer_quality(body, current_user=user, db=SimpleNamespace())
    assert exc.value.status_code == 403
    assert body_project_check.await_args.args[1:] == (user, body.project_id)
    dependency = inspect.signature(router.run_reviewer_quality).parameters["current_user"].default.dependency
    assert router.UserRole.QA_LEAD in [cell.cell_contents for cell in (dependency.__closure__ or ())]
