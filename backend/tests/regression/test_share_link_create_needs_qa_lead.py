"""Creating a public share link for a run needs QA_LEAD, not just membership.

A share link is an unauthenticated URL: anyone holding it reads the run report
for up to 30 days, outside every access control. ``POST
/api/v1/reports/runs/{run_id}/share`` checked project membership only, so a
VIEWER published a report, while e-mailing the same report already needed
QA_LEAD "because the report leaves the system".

The rule this file pins:

* VIEWER, TESTER and QA_ENGINEER members get 403 ``Requires at least QA_LEAD
  role`` and no link is created, no audit row staged, nothing committed;
* a QA_LEAD member gets 201 and the token;
* the run guard still decides tenancy: a QA_LEAD who is not a member of the
  run's project gets its 404 and no link;
* a project-bound API key keeps working for its own project (the run guard
  confines it) and is refused on another project's run;
* listing a run's links stays open to every member.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.regression

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER = uuid.UUID("22222222-2222-4222-8222-222222222222")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")
NOW = datetime(2026, 11, 19, tzinfo=timezone.utc)


def _user(role: str, *, bound_to: uuid.UUID | None = None):
    user = SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True, username=role.lower())
    if bound_to is not None:
        # How deps marks a project-bound API key (``_API_KEY_BOUND_PROJECT_ATTR``).
        setattr(user, "_testlookup_api_key_project_id", bound_to)
    return user


def _scalar(value):
    return MagicMock(scalar_one_or_none=MagicMock(return_value=value))


def _link():
    return SimpleNamespace(
        id=uuid.uuid4(), report_layout="engineering", expires_at=NOW + timedelta(days=30),
        created_by_name="lead", access_count=0, is_revoked=False, created_at=NOW,
    )


@pytest.fixture
def world(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import reports

    state = SimpleNamespace(user=_user("VIEWER"), member=True)

    async def _execute(statement, *args, **kwargs):
        text = str(statement).lower()
        if "run_intelligence_snapshots" in text:
            return _scalar({"summary": "ready"})
        if "project_members" in text:
            return _scalar(uuid.uuid4() if state.member else None)
        if "test_runs" in text:
            return _scalar(PROJECT)
        raise AssertionError(f"unexpected query: {text}")

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    state.create = AsyncMock(return_value=SimpleNamespace(link=_link(), raw_token="tok-fake-0001"))
    state.list = AsyncMock(return_value=[_link()])
    state.audit = MagicMock()
    monkeypatch.setattr("app.services.share_link_service.create_share_link", state.create)
    monkeypatch.setattr("app.services.share_link_service.list_share_links", state.list)
    monkeypatch.setattr(reports, "record_activity", AsyncMock())
    monkeypatch.setattr(reports, "_stage_audit", state.audit)

    async def _db():
        yield db

    app = FastAPI()
    app.include_router(reports.router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state.user
    state.db = db
    state.http = TestClient(app, raise_server_exceptions=False)
    return state


def _share(world):
    return world.http.post(
        f"/api/v1/reports/runs/{RUN}/share", json={"layout": "engineering", "expiry_days": 30}
    )


def _assert_no_link(world):
    world.create.assert_not_awaited()
    world.audit.assert_not_called()
    world.db.commit.assert_not_awaited()


@pytest.mark.parametrize("role", ["VIEWER", "TESTER", "QA_ENGINEER"])
def test_a_member_below_qa_lead_cannot_create_a_share_link(world, role):
    world.user = _user(role)

    resp = _share(world)

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Requires at least QA_LEAD role"
    _assert_no_link(world)


@pytest.mark.parametrize("role", ["QA_LEAD", "ADMIN"])
def test_a_qa_lead_member_creates_a_share_link(world, role):
    world.user = _user(role)

    resp = _share(world)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["token"] == "tok-fake-0001"
    assert body["share_url"].endswith("/api/v1/shared/reports/tok-fake-0001")
    world.create.assert_awaited_once()
    world.db.commit.assert_awaited_once()


def test_a_qa_lead_of_another_project_still_gets_the_run_guards_refusal(world):
    world.user = _user("QA_LEAD")
    world.member = False

    resp = _share(world)

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Test run not found"
    _assert_no_link(world)


def test_a_key_bound_to_the_runs_project_still_creates_a_link(world):
    """The route opts a project-bound key in: the run guard confines it."""
    world.user = _user("ADMIN", bound_to=PROJECT)

    resp = _share(world)

    assert resp.status_code == 201, resp.text
    world.create.assert_awaited_once()


def test_a_key_bound_to_another_project_cannot_create_a_link(world):
    world.user = _user("ADMIN", bound_to=OTHER)

    resp = _share(world)

    assert resp.status_code == 404, resp.text
    _assert_no_link(world)


@pytest.mark.parametrize("role", ["VIEWER", "TESTER"])
def test_listing_a_runs_share_links_stays_open_to_every_member(world, role):
    world.user = _user(role)

    resp = world.http.get(f"/api/v1/reports/runs/{RUN}/share-links")

    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1 and resp.json()[0]["token"] is None
