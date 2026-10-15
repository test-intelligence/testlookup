"""DEF-01: the triage PATCH bumps the project's analytics epoch AFTER its commit, never on a 4xx.

The Defects list, the KPI tiles, the trend and the gate's cached counts read
these rows through ``analytics_read``; a change the epoch does not announce is
served stale until the TTL. Same journal style as
``tests/regression/test_analytics_epoch_mutation_paths.py``: commits and bumps
land in ONE journal and the bump must follow the commit, for the defect's
project. The static half is the ``backend.analytics-epoch-bump`` gate, whose
registry names ``defect_triage_service.update_defect``.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.models.postgres import UserRole
from app.routers import defect_jira as defect_jira_router
from tests.fakes.fake_defect_world import (
    HttpRecorder,
    MemDB,
    defect,
    make_app,
    membership,
    patch_jira_config,
    user,
)

pytestmark = pytest.mark.asyncio

PID = uuid.uuid4()


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    patch_jira_config(monkeypatch)
    HttpRecorder().install(monkeypatch)
    engineer = user(UserRole.QA_ENGINEER)
    row = defect(PID, test_case_id=uuid.uuid4())
    journal: list[str] = []
    db = MemDB([engineer, row, membership(engineer, PID)], journal=journal)

    async def _bump(project_id):
        journal.append(f"bump {project_id}")

    async def _row(session, project_id, defect_id):
        return {"id": str(defect_id)}

    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", _bump)
    monkeypatch.setattr("app.services.analytics_service.defect_row", _row, raising=False)

    async def patch(body, defect_id=None):
        app = make_app(defect_jira_router.router, lambda: db, engineer)
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                               base_url="http://t") as client:
            return await asyncio.wait_for(
                client.patch(f"/api/v1/projects/{PID}/defects/{defect_id or row.id}", json=body), timeout=5,
            )

    return db, row, journal, patch


def _bumps(journal):
    return [e for e in journal if e.startswith("bump")]


@pytest.mark.parametrize(
    "body",
    [{"resolution_status": "RESOLVED"}, {"owner_team": "Payments"}, {"jira_key": "ABC-1"}],
    ids=["status", "team", "link"],
)
async def test_a_change_bumps_its_project_after_the_commit(world, body):
    db, _row, journal, patch = world

    response = await patch(body)

    assert response.status_code == 200, response.text
    assert _bumps(journal) == [f"bump {PID}"]
    assert journal.index("commit") < journal.index(f"bump {PID}")


@pytest.mark.parametrize(
    ("body", "code"),
    [({}, 422), ({"resolution_status": "CLOSED"}, 422), ({"jira_key": "not a key"}, 422),
     ({"assignee_user_id": str(uuid.uuid4())}, 422)],
)
async def test_a_refused_patch_never_bumps(world, body, code):
    _db, _row, journal, patch = world

    response = await patch(body)

    assert response.status_code == code
    assert _bumps(journal) == []
    assert "commit" not in journal


async def test_a_missing_defect_never_bumps(world):
    _db, _row, journal, patch = world

    response = await patch({"resolution_status": "RESOLVED"}, defect_id=uuid.uuid4())

    assert response.status_code == 404
    assert _bumps(journal) == []


async def test_a_conflict_never_bumps(world):
    db, row, journal, patch = world
    row.resolution_status = "RESOLVED"
    db.rows.append(defect(PID, test_case_id=row.test_case_id, resolution_status="OPEN"))

    response = await patch({"resolution_status": "OPEN"})

    assert response.status_code == 409
    assert _bumps(journal) == []


async def test_a_no_op_patch_commits_but_does_not_bump(world):
    _db, _row, journal, patch = world

    response = await patch({"resolution_status": "OPEN"})

    assert response.status_code == 200
    assert response.json()["changed_fields"] == []
    assert _bumps(journal) == []
