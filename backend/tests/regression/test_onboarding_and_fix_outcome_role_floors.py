"""Onboarding step writes and fix outcomes need a role, not just membership.

S-D-7. ``POST /onboarding/{project}/skip`` and ``/restore`` and ``/complete``
checked project membership only, so a VIEWER could mark the team's setup steps
skipped or done for everyone. ``POST /projects/{project}/fix-outcomes`` let any
member write an ``ai_feedback`` row that becomes a ``human_indirect`` training
label, while every other label-planting write needs QA_ENGINEER.

The rule this file pins:

* skip and restore: QA_LEAD (they change what the whole team's checklist says);
* complete: any member for ``view_intelligence`` (the run page reports it when a
  reader opens Analysis), QA_ENGINEER for every other step;
* fix-outcomes: QA_ENGINEER.

A role below the floor gets 403 and the service is never called; the floor
role reaches it once.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.regression

P = uuid.UUID("11111111-1111-4111-8111-111111111111")
ROLES = ["VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD", "ADMIN"]
STATUS = {
    "project_id": str(P), "steps": [], "completed_count": 0,
    "total_count": 0, "progress_pct": 0, "is_complete": False,
}


def _user(role: str):
    return SimpleNamespace(
        id=uuid.uuid4(), role=role, is_active=True, api_key_project_id=None,
        full_name=f"{role} person", username=role.lower(),
    )


@pytest.fixture
def world(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.core.deps as deps
    from app.db.postgres import get_db
    from app.routers import feedback, onboarding

    state = SimpleNamespace(user=_user("VIEWER"))
    reached = {
        "complete_step": AsyncMock(return_value=STATUS),
        "skip_step": AsyncMock(return_value=STATUS),
        "restore_step": AsyncMock(return_value=STATUS),
        "record_fix_outcome": AsyncMock(return_value={"id": str(uuid.uuid4())}),
    }
    for name in ("complete_step", "skip_step", "restore_step"):
        monkeypatch.setattr(onboarding, name, reached[name])
    monkeypatch.setattr(feedback.feedback_service, "record_fix_outcome", reached["record_fix_outcome"])

    async def _execute(statement, *args, **kwargs):
        # Every membership read (the access check, the project role floor and
        # the body-dependent check) answers with the caller's own role.
        answer = MagicMock()
        answer.scalar_one_or_none.return_value = str(state.user.role)
        return answer

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    async def _db():
        yield db

    app = FastAPI()
    app.include_router(onboarding.router)
    app.include_router(feedback.lookup_router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[deps.get_current_active_user] = lambda: state.user
    return SimpleNamespace(client=TestClient(app), state=state, reached=reached)


#: (path, body, floor role, mock the handler reaches)
ROUTES = [
    (f"/api/v1/onboarding/{P}/skip", {"step_key": "connect_ci"}, "QA_LEAD", "skip_step"),
    (f"/api/v1/onboarding/{P}/restore", {"step_key": "connect_ci"}, "QA_LEAD", "restore_step"),
    (f"/api/v1/onboarding/{P}/complete", {"step_key": "connect_ci"}, "QA_ENGINEER", "complete_step"),
    (f"/api/v1/onboarding/{P}/complete", {"step_key": "view_intelligence"}, "VIEWER", "complete_step"),
    (f"/api/v1/projects/{P}/fix-outcomes", {"fingerprint": "abc123", "outcome": "fixed"}, "QA_ENGINEER",
     "record_fix_outcome"),
]
IDS = [f"{r[0].rsplit('/', 1)[-1]}:{r[1].get('step_key', '')}" for r in ROUTES]


@pytest.mark.parametrize("path,body,floor,mock_name", ROUTES, ids=IDS)
def test_each_role_below_the_floor_is_refused_and_the_floor_reaches_the_service(
    world, path, body, floor, mock_name
):
    for role in ROLES:
        world.state.user = _user(role)
        world.reached[mock_name].reset_mock()
        resp = world.client.post(path, json=body)
        if ROLES.index(role) < ROLES.index(floor):
            assert resp.status_code == 403, (role, resp.status_code, resp.text)
            world.reached[mock_name].assert_not_called()
        else:
            assert resp.status_code in (200, 201), (role, resp.status_code, resp.text)
            world.reached[mock_name].assert_awaited_once()
