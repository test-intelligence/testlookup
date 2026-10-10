"""E2E pass 2026-10-10 (homelab): two permission leaks found by calling the API
as every role, not as admin.

1. ``POST /api/v1/projects/{id}/default-qa-lead/reset-password`` had only
   ``require_project_access()``. A VIEWER member got ``200`` and the plaintext
   password of the project's synthetic QA_LEAD account, i.e. a QA lead login.
   It now needs QA_LEAD, like the Projects -> Members page that offers it.

2. ``/api/v1/stream/sessions`` (POST, GET, DELETE) checked a project-bound
   API key's binding and nothing else. A JWT or a user-scoped key carries no
   binding, so a user who is not a member of a project opened a live run in it
   (by UUID or by NAME), read another project's session and closed it. Live:
   ``qa_engineer`` (403 on ``POST /ingest`` to the project) got ``201`` and a
   session token for the same project, then ``204`` closing an admin's session.
   The architectural-authorization exemption for GET/DELETE claimed the service
   checked membership; it did not.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.regression

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER = uuid.UUID("22222222-2222-4222-8222-222222222222")


def _user(role: str):
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True, api_key_project_id=None)


# ── 1. default QA lead password reset ───────────────────────────────────────


@pytest.fixture
def reset_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import projects

    app = FastAPI()
    app.include_router(projects.router)
    db = AsyncMock()
    # Membership row and project row both "found".
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(
        return_value=SimpleNamespace(id=PROJECT))))

    async def _db():
        yield db

    state = {"user": _user("VIEWER")}
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state["user"]
    fake = AsyncMock(return_value=(SimpleNamespace(id=uuid.uuid4(), email="e", username="u"), "pw"))
    with patch("app.services.default_qa_lead_service.reset_default_qa_lead_password", fake):
        yield TestClient(app, raise_server_exceptions=False), state, fake


@pytest.mark.parametrize("role", ["VIEWER", "TESTER", "QA_ENGINEER"])
def test_a_member_below_qa_lead_cannot_get_the_qa_lead_password(reset_client, role):
    http, state, fake = reset_client
    state["user"] = _user(role)
    resp = http.post(f"/api/v1/projects/{PROJECT}/default-qa-lead/reset-password")
    assert resp.status_code == 403
    assert "password" not in resp.text
    fake.assert_not_awaited()


@pytest.mark.parametrize("role", ["QA_LEAD", "ADMIN"])
def test_a_qa_lead_member_still_rotates_it(reset_client, role):
    http, state, fake = reset_client
    state["user"] = _user(role)
    resp = http.post(f"/api/v1/projects/{PROJECT}/default-qa-lead/reset-password")
    assert resp.status_code == 200, resp.text
    assert resp.json()["password"] == "pw"
    fake.assert_awaited_once()


# ── 2. live-stream sessions ────────────────────────────────────────────────


def _accessible(ids):
    return patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=ids))


@pytest.mark.asyncio
async def test_access_helper_refuses_a_non_member_and_admits_member_admin_and_system():
    from app.services.stream_service import assert_session_project_access

    db = AsyncMock()
    with _accessible({OTHER}):
        with pytest.raises(HTTPException) as exc:
            await assert_session_project_access(db, PROJECT, user=_user("QA_ENGINEER"))
        assert exc.value.status_code == 403
        with pytest.raises(HTTPException) as exc:
            await assert_session_project_access(
                db, PROJECT, user=_user("QA_ENGINEER"), not_found_detail="Session not found",
            )
        assert exc.value.status_code == 404
    with _accessible({PROJECT}):
        await assert_session_project_access(db, PROJECT, user=_user("VIEWER"))
    with _accessible(None):  # ADMIN
        await assert_session_project_access(db, PROJECT, user=_user("ADMIN"))
    # System callers (the reaper, the API-key stream path) pass no user.
    await assert_session_project_access(db, PROJECT)
    # A bound key is confined to its project, whatever its owner's memberships.
    with pytest.raises(HTTPException) as exc:
        await assert_session_project_access(db, PROJECT, user=_user("ADMIN"), bound_project_id=OTHER)
    assert exc.value.status_code == 403


def _payload(identifier: str):
    return SimpleNamespace(
        project_id=identifier, client_name="sdk", machine_id=None, build_number="b1",
        framework="pytest", branch=None, commit_hash=None, total_tests=1, release_name=None,
        launch_name=None, suite_name=None, metadata={},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier, code", [(str(PROJECT), 403), ("Someone Else's Project", 404)])
async def test_a_non_member_cannot_open_a_live_run_in_the_project(identifier, code):
    from app.services import stream_service

    db = AsyncMock()
    db.add = MagicMock()
    project = SimpleNamespace(id=PROJECT, name="Someone Else's Project")
    with patch.object(stream_service, "resolve_project", AsyncMock(return_value=project)), \
            _accessible({OTHER}):
        with pytest.raises(HTTPException) as exc:
            await stream_service.create_session(db, _payload(identifier), user=_user("QA_ENGINEER"))
    assert exc.value.status_code == code
    # Refused before anything was staged: no LiveSession, no TestRun stub.
    db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("fn", ["get_session", "close_session"])
async def test_a_non_member_cannot_read_or_close_another_projects_session(fn):
    from app.services import stream_service

    session = SimpleNamespace(id=uuid.uuid4(), project_id=PROJECT, status="active")
    db = AsyncMock()
    db.get = AsyncMock(return_value=session)
    with _accessible({OTHER}):
        with pytest.raises(HTTPException) as exc:
            await getattr(stream_service, fn)(db, str(session.id), user=_user("VIEWER"))
    assert exc.value.status_code == 404
    assert exc.value.detail == "Session not found"


def test_the_routes_hand_the_caller_to_the_service():
    """The service can only check what it is given: every session route passes
    the authenticated user, not just the key binding."""
    import inspect

    from app.routers import stream

    for handler in (stream.create_session, stream.get_session, stream.close_session):
        assert "user=current_user" in inspect.getsource(handler), handler.__name__
