"""The project "default QA lead" password reset acts on the shared account only.

``POST /api/v1/projects/{id}/default-qa-lead/reset-password`` exists for the
auto-provisioned, shared ``qalead-<slug>`` triage account
(``User.is_synthetic``). ``Project.default_qa_lead_user_id`` may also name a
real person (suite-review routing uses it that way), so the reset must refuse
any account that is not synthetic: it replaces the password and returns the new
one in the response, which is only acceptable for a shared account.

The refusal lives in the service so that no second caller can skip it; the
route maps it to 409 and never returns a ``password`` field for it.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.regression

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")


@pytest.fixture
def no_revocation(monkeypatch):
    import app.services.default_qa_lead_service as svc

    calls = {"access": 0, "refresh": 0}

    async def _access(user_id, db=None):
        calls["access"] += 1

    async def _refresh(db, user_id, reason="revoked"):
        calls["refresh"] += 1

    monkeypatch.setattr(svc, "revoke_all_user_tokens", _access)
    monkeypatch.setattr(svc, "_revoke_refresh_family", _refresh)
    return calls


def _user(*, role: str, is_synthetic: bool):
    return SimpleNamespace(
        id=uuid.uuid4(),
        role=role,
        email="someone@example.test",
        username="someone",
        is_synthetic=is_synthetic,
        hashed_password="original-hash",
        must_change_password=False,
    )


def _project(user):
    return SimpleNamespace(id=PROJECT, slug="demo", name="Demo", default_qa_lead_user_id=user.id)


def _db(user):
    db = AsyncMock()
    db.get = AsyncMock(return_value=user)
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["ADMIN", "QA_LEAD"])
async def test_reset_refuses_a_personal_account(no_revocation, role):
    from app.services.default_qa_lead_service import (
        DefaultQaLeadIsPersonalAccount,
        reset_default_qa_lead_password,
    )

    person = _user(role=role, is_synthetic=False)

    with pytest.raises(DefaultQaLeadIsPersonalAccount):
        await reset_default_qa_lead_password(_db(person), _project(person))

    assert person.hashed_password == "original-hash"
    assert no_revocation == {"access": 0, "refresh": 0}


@pytest.mark.asyncio
async def test_reset_still_rotates_the_shared_account(no_revocation):
    from app.services.default_qa_lead_service import reset_default_qa_lead_password

    shared = _user(role="QA_LEAD", is_synthetic=True)

    user, password = await reset_default_qa_lead_password(_db(shared), _project(shared))

    assert user is shared
    assert password and len(password) >= 24
    assert shared.hashed_password != "original-hash"
    assert no_revocation == {"access": 1, "refresh": 1}


# ── the route ────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import projects

    app = FastAPI()
    app.include_router(projects.router)
    caller = SimpleNamespace(
        id=uuid.uuid4(), role="QA_LEAD", username="lead", is_active=True, api_key_project_id=None,
    )
    session = AsyncMock()
    session.add = MagicMock()
    # Membership row and project row both "found".
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(
        return_value=SimpleNamespace(id=PROJECT, slug="demo", name="Demo"))))

    async def _get_db():
        yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: caller
    yield TestClient(app, raise_server_exceptions=False), session, caller


def test_route_answers_409_without_a_password_for_a_personal_account(client):
    from app.services.default_qa_lead_service import DefaultQaLeadIsPersonalAccount

    http, session, _ = client
    refused = AsyncMock(side_effect=DefaultQaLeadIsPersonalAccount())
    with patch("app.services.default_qa_lead_service.reset_default_qa_lead_password", refused):
        resp = http.post(f"/api/v1/projects/{PROJECT}/default-qa-lead/reset-password")

    assert resp.status_code == 409, resp.text
    assert "password" not in resp.json()
    session.commit.assert_not_awaited()


def test_route_records_who_reset_the_shared_account(client):
    http, session, caller = client
    target = SimpleNamespace(id=uuid.uuid4(), email="qalead-demo@example.test", username="qalead-demo")
    ok = AsyncMock(return_value=(target, "new-shared-secret"))
    with patch("app.services.default_qa_lead_service.reset_default_qa_lead_password", ok):
        resp = http.post(f"/api/v1/projects/{PROJECT}/default-qa-lead/reset-password")

    assert resp.status_code == 200, resp.text
    assert resp.json()["password"] == "new-shared-secret"
    audit_rows = [c.args[0] for c in session.add.call_args_list if type(c.args[0]).__name__ == "AccessAuditLog"]
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row.action == "default_qa_lead_password_reset"
    assert row.actor_user_id == caller.id and row.target_user_id == target.id and row.project_id == PROJECT
    assert "new-shared-secret" not in repr((row.before_value, row.after_value))
    session.commit.assert_awaited_once()
    # D-82: a one-time password must not be kept by any cache.
    assert resp.headers.get("cache-control") == "no-store"


def test_s_d_3_route_answers_409_for_an_archived_project(client):
    from app.services.default_qa_lead_service import DefaultQaLeadProjectArchived

    http, session, _ = client
    refused = AsyncMock(side_effect=DefaultQaLeadProjectArchived())
    with patch("app.services.default_qa_lead_service.reset_default_qa_lead_password", refused):
        resp = http.post(f"/api/v1/projects/{PROJECT}/default-qa-lead/reset-password")

    assert resp.status_code == 409, resp.text
    assert "archived" in resp.json()["detail"]
    assert "password" not in resp.json()
    session.commit.assert_not_awaited()
