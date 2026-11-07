"""W36 STRAT-03 — the three cluster-epic routes through FastAPI's real role and project guards.

A mini app mounts ``routers/defect_jira.py`` alone; only ``get_current_active_user`` and ``get_db`` are
overridden, so ``require_role(QA_ENGINEER)`` and ``require_project_access()`` run for real (the membership
lookup answers from the fake session). The service functions are recorders: the routes' own contract is
under test here — roles, the project-access refusal, the path pattern, 201 vs 200, the router's commit and
the response shapes. The service itself: ``tests/test_cluster_epic_service.py``; on PostgreSQL:
``tests/integration/test_cluster_epic_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.deps import CREDENTIAL_KIND_JWT, _bind_credential_kind, get_current_active_user, get_db  # noqa: E402
from app.routers import defect_jira as route  # noqa: E402
from app.services import cluster_epic_service as ces  # noqa: E402

PROJECT = uuid.UUID("36a00000-0000-4000-8000-0000000000aa")
KEY = "0123456789abcdef0123456789abcdef"
BASE = f"/api/v1/projects/{PROJECT}/systemic-clusters"


class _Result:
    def __init__(self, scalar: Any = None) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _Session:
    """Answers the project-membership lookup; journals commits."""

    def __init__(self, *, member: bool = True) -> None:
        self.member = member
        self.commits = 0

    async def execute(self, stmt: Any, *_a: Any, **_kw: Any) -> _Result:
        if "project_members" in str(stmt):
            return _Result(uuid.uuid4() if self.member else None)
        return _Result()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:  # pragma: no cover - the routes never roll back
        pass

    async def close(self) -> None:  # pragma: no cover
        pass


def _user(role: str) -> SimpleNamespace:
    user = SimpleNamespace(id=uuid.uuid4(), role=role, full_name="Qa Person", username="qa",
                           email="qa@example.invalid", is_active=True)
    _bind_credential_kind(user, CREDENTIAL_KIND_JWT)
    return user


def _client(session: _Session, user: SimpleNamespace) -> AsyncClient:
    app = FastAPI()
    app.include_router(route.router)

    async def _db():
        yield session

    async def _current():
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = _current
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture
def calls(monkeypatch):
    """Recorders for the three service entry points (created comes from ``state['created']``)."""
    state: dict[str, Any] = {"created": True, "create": [], "status": [], "preview": []}

    async def _create(db, project_id, actor, membership_key, **kwargs):
        state["create"].append({"project_id": project_id, "actor": actor, "key": membership_key, **kwargs})
        return {"membership_key": membership_key, "jira_key": "DEF-42", "jira_url": "https://j/browse/DEF-42",
                "created": state["created"]}

    async def _status(db, project_id, *, user=None):
        state["status"].append(project_id)
        return {"enabled": True, "available": False, "reason": ces.REASON_SENTENCES["offline_mode"],
                "epic_type_available": None, "min_co_failure_runs": 3,
                "links": [{"membership_key": KEY, "jira_key": "DEF-1", "jira_url": None, "cluster_label": "c",
                           "created_at": datetime(2026, 11, 6, tzinfo=timezone.utc), "created_by_name": None}]}

    async def _preview(db, project_id, membership_key, *, actor=None):
        state["preview"].append(membership_key)
        state["preview_actor"] = actor
        return {"membership_key": membership_key, "summary": "[TestLookup] Recurring failure cluster: c",
                "description_text": "d", "labels": list(ces.EPIC_LABELS), "jira_project_key": None,
                "member_count": 3, "co_failure_runs": 4, "cluster_label": "c"}

    monkeypatch.setattr(ces, "create_epic_for_cluster", _create)
    monkeypatch.setattr(ces, "get_epic_status", _status)
    monkeypatch.setattr(ces, "preview_epic", _preview)
    return state


# ── the POST: roles, membership, 201 / 200, the router's commit ──────────────


@pytest.mark.parametrize("role", ["VIEWER", "TESTER"])
async def test_a_viewer_or_tester_cannot_file_an_epic(calls, role):
    session = _Session()
    async with _client(session, _user(role)) as client:
        response = await client.post(f"{BASE}/{KEY}/epic", json={})
    assert response.status_code == 403 and response.json()["detail"] == "Requires at least QA_ENGINEER role"
    assert calls["create"] == [] and session.commits == 0


async def test_a_non_member_engineer_gets_the_project_access_refusal(calls):
    session = _Session(member=False)
    async with _client(session, _user("QA_ENGINEER")) as client:
        response = await client.post(f"{BASE}/{KEY}/epic", json={})
    assert response.status_code == 403 and response.json()["detail"] == "You do not have access to this project"
    assert calls["create"] == [] and session.commits == 0


async def test_a_member_engineer_files_201_and_the_router_commits(calls):
    session, user = _Session(), _user("QA_ENGINEER")
    body = {"jira_project_key": "OPS", "summary": "Gateway epic", "confirm_not_filed": True}
    async with _client(session, user) as client:
        response = await client.post(f"{BASE}/{KEY}/epic", json=body)
    assert response.status_code == 201
    assert response.json() == {"membership_key": KEY, "jira_key": "DEF-42", "jira_url": "https://j/browse/DEF-42",
                               "created": True}
    (call,) = calls["create"]
    assert (call["project_id"], call["actor"], call["key"]) == (PROJECT, user, KEY)
    assert (call["jira_project_key"], call["summary"], call["confirm_not_filed"]) == ("OPS", "Gateway epic", True)
    assert session.commits == 1


async def test_a_stored_epic_answers_200_created_false(calls):
    calls["created"] = False
    async with _client(_Session(), _user("QA_LEAD")) as client:
        response = await client.post(f"{BASE}/{KEY}/epic", json={})
    assert response.status_code == 200 and response.json()["created"] is False
    (call,) = calls["create"]
    assert (call["jira_project_key"], call["summary"], call["confirm_not_filed"]) == (None, None, False)


@pytest.mark.parametrize("bad", ["NOT-A-KEY", "0123456789ABCDEF0123456789ABCDEF", "0123456789abcdef"])
async def test_a_malformed_membership_key_is_refused_before_the_service(calls, bad):
    async with _client(_Session(), _user("ADMIN")) as client:
        post = await client.post(f"{BASE}/{bad}/epic", json={})
        preview = await client.get(f"{BASE}/{bad}/epic/preview")
    assert post.status_code == 422 and preview.status_code == 422
    assert calls["create"] == [] and calls["preview"] == []


async def test_a_summary_over_255_is_refused(calls):
    async with _client(_Session(), _user("QA_ENGINEER")) as client:
        response = await client.post(f"{BASE}/{KEY}/epic", json={"summary": "s" * 256})
    assert response.status_code == 422 and calls["create"] == []


# ── the two reads: any member, no commit ─────────────────────────────────────


async def test_a_viewer_member_reads_the_status_and_the_preview(calls):
    session, viewer = _Session(), _user("VIEWER")
    async with _client(session, viewer) as client:
        status = await client.get(f"{BASE}/epics")
        preview = await client.get(f"{BASE}/{KEY}/epic/preview")
    assert status.status_code == 200 and preview.status_code == 200
    assert set(status.json()) == {"enabled", "available", "reason", "epic_type_available", "min_co_failure_runs",
                                  "links"}
    assert status.json()["links"][0]["created_by_name"] is None
    assert set(preview.json()) == {"membership_key", "summary", "description_text", "labels", "jira_project_key",
                                   "member_count", "co_failure_runs", "cluster_label"}
    assert calls["status"] == [PROJECT] and calls["preview"] == [KEY] and session.commits == 0
    assert calls["preview_actor"] is viewer          # the preview names the person who would file it


async def test_a_non_member_reads_nothing(calls):
    async with _client(_Session(member=False), _user("QA_LEAD")) as client:
        status = await client.get(f"{BASE}/epics")
        preview = await client.get(f"{BASE}/{KEY}/epic/preview")
    assert status.status_code == 403 and preview.status_code == 403
    assert calls["status"] == [] and calls["preview"] == []


# ── the opt-in rides the existing project PUT (require_case_review's pattern) ─


def test_the_opt_in_is_sent_only_when_given_and_false_is_kept():
    from app.models.schemas import ProjectUpdate

    assert ProjectUpdate(cluster_epic_suggestions=False).model_dump(exclude_none=True) == {
        "cluster_epic_suggestions": False}
    assert "cluster_epic_suggestions" not in ProjectUpdate(description="x").model_dump(exclude_none=True)


def test_the_project_response_reads_off_for_an_unflushed_or_hand_built_row():
    from app.models.postgres import Project
    from app.models.schemas import ProjectResponse

    row = SimpleNamespace(id=uuid.uuid4(), name="P", slug="p", is_active=True,
                          created_at=datetime(2026, 11, 6, tzinfo=timezone.utc), cluster_epic_suggestions=True)
    assert ProjectResponse.model_validate(row).cluster_epic_suggestions is True
    del row.cluster_epic_suggestions
    assert ProjectResponse.model_validate(row).cluster_epic_suggestions is False
    unflushed = Project(id=uuid.uuid4(), name="N", slug="n", is_active=True,
                        created_at=datetime(2026, 11, 6, tzinfo=timezone.utc))
    assert unflushed.cluster_epic_suggestions is None, "the ORM default applies only at INSERT"
    assert ProjectResponse.model_validate(unflushed).cluster_epic_suggestions is False


async def test_the_existing_put_turns_it_on_and_records_project_updated():
    from unittest.mock import AsyncMock, MagicMock

    from app.models.postgres import ProjectActivityEvent
    from app.models.schemas import ProjectUpdate
    from app.routers.projects import update_project

    project = SimpleNamespace(id=PROJECT, name="Payments", slug="payments", is_active=True,
                              created_at=datetime(2026, 11, 1, tzinfo=timezone.utc), cluster_epic_suggestions=False)
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=project)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    actor = SimpleNamespace(id=uuid.uuid4(), full_name="Lee Lead", username="lead", role="QA_LEAD", api_key_id=None)

    await update_project(PROJECT, ProjectUpdate(cluster_epic_suggestions=True), db, actor)

    assert project.cluster_epic_suggestions is True
    db.commit.assert_awaited_once()
    (row,), _ = db.add.call_args
    assert isinstance(row, ProjectActivityEvent) and row.event_type == "project.updated"
    assert row.context["changed"] == "cluster_epic_suggestions"
