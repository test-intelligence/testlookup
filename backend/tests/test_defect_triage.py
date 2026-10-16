"""DEF-01: ``PATCH /api/v1/projects/{project_id}/defects/{defect_id}`` — authorization, assignee, re-open, ledger.

Drives the REAL route, guards and triage service over an in-memory session
that evaluates the real queries (``tests/fakes/fake_defect_world.MemDB``), so
a project filter dropped from ``get_defect_in_project`` or from the assignee
membership check fails here. Jira is offline (the demonstrated default) unless
a test says otherwise; Jira behaviour lives in
``test_defect_jira_link_and_transition.py``, the real-Postgres proof in
``tests/integration/test_defect_triage_postgres.py``.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.deps import _bind_api_key_project
from app.models.postgres import ProjectActivityEvent, UserRole
from app.routers import defect_jira as defect_jira_router
from app.services import defect_triage_service as triage
from app.services.defect_status import STATUS_INVALID_DETAIL
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
OTHER_PID = uuid.uuid4()


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    patch_jira_config(monkeypatch)
    http = HttpRecorder().install(monkeypatch)
    engineer = user(UserRole.QA_ENGINEER, name="Quinn Engineer")
    priya = user(UserRole.TESTER, name="Priya Nair")
    stranger = user(UserRole.QA_ENGINEER, name="Sam Elsewhere")
    test_case = uuid.uuid4()
    row = defect(PID, test_case_id=test_case)
    db = MemDB([
        engineer, priya, stranger, row,
        membership(engineer, PID), membership(priya, PID), membership(stranger, OTHER_PID),
    ])
    bumps: list = []

    async def _bump(project_id):
        bumps.append(project_id)
        db.journal.append("bump")

    async def _row(session, project_id, defect_id):
        found = next(d for d in db.rows if getattr(d, "id", None) == uuid.UUID(str(defect_id)))
        return {"id": str(found.id), "project_id": str(found.project_id),
                "resolution_status": found.resolution_status,
                "assignee_user_id": str(found.assignee_user_id) if found.assignee_user_id else None}

    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", _bump)
    monkeypatch.setattr("app.services.analytics_service.defect_row", _row, raising=False)
    w = SimpleNamespace(db=db, http=http, engineer=engineer, priya=priya, stranger=stranger,
                        defect=row, test_case=test_case, bumps=bumps, principal=engineer)

    async def patch(body, *, principal=None, project_id=PID, defect_id=None):
        app = make_app(defect_jira_router.router, lambda: db, principal or w.principal)
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                               base_url="http://t") as client:
            return await asyncio.wait_for(client.patch(
                f"/api/v1/projects/{project_id}/defects/{defect_id or row.id}", json=body,
            ), timeout=5)

    w.patch = patch
    return w


def _ledger(db) -> list[ProjectActivityEvent]:
    return [r for r in db.added if isinstance(r, ProjectActivityEvent)]


# ── 1. authorization: every id is checked, one 404 text ────────────────────


async def test_another_projects_defect_is_the_same_404_as_a_random_id(world):
    foreign = defect(OTHER_PID)
    world.db.rows.append(foreign)

    via_foreign = await world.patch({"resolution_status": "RESOLVED"}, defect_id=foreign.id)
    via_random = await world.patch({"resolution_status": "RESOLVED"}, defect_id=uuid.uuid4())

    assert via_foreign.status_code == 404
    assert via_foreign.json()["detail"] == "Defect not found in this project."
    assert via_random.json() == via_foreign.json()
    assert foreign.resolution_status == "OPEN"


@pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.TESTER])
async def test_viewers_and_testers_are_refused(world, role):
    member = user(role)
    world.db.rows += [member, membership(member, PID)]

    response = await world.patch({"resolution_status": "RESOLVED"}, principal=member)

    assert response.status_code == 403
    assert world.defect.resolution_status == "OPEN"


async def test_a_qa_engineer_who_is_not_a_member_is_refused(world):
    response = await world.patch({"resolution_status": "RESOLVED"}, principal=world.stranger)

    assert response.status_code == 403
    assert world.defect.resolution_status == "OPEN"


async def test_an_api_key_bound_to_another_project_is_refused(world):
    _bind_api_key_project(world.engineer, OTHER_PID)

    response = await world.patch({"resolution_status": "RESOLVED"})

    assert response.status_code == 403
    assert world.defect.resolution_status == "OPEN"


async def test_nothing_to_change_is_a_422(world):
    for body in ({}, {"note": "just a note"}):
        response = await world.patch(body)
        assert response.status_code == 422
        assert response.json()["detail"] == "Nothing to change."


async def test_vocabulary_is_one_list(world):
    for word in ("CLOSED", "DONE"):
        response = await world.patch({"resolution_status": word})
        assert response.status_code == 422
        assert response.json()["detail"] == STATUS_INVALID_DETAIL
    accepted = await world.patch({"resolution_status": "in_progress"})
    assert accepted.status_code == 200, accepted.text
    assert world.defect.resolution_status == "IN_PROGRESS"


# ── 2. assignee: one 422 text, ADMIN without membership, clear, no-op ───────


@pytest.mark.parametrize("case", ["other_project", "inactive", "service", "synthetic", "unknown", "malformed"])
async def test_every_ineligible_assignee_gets_one_text(world, case):
    if case == "other_project":
        value = str(world.stranger.id)
    elif case in ("inactive", "service", "synthetic"):
        person = user(UserRole.QA_ENGINEER, active=case != "inactive",
                      service=case == "service", synthetic=case == "synthetic")
        world.db.rows += [person, membership(person, PID)]
        value = str(person.id)
    elif case == "unknown":
        value = str(uuid.uuid4())
    else:
        value = "not-a-uuid"

    response = await world.patch({"assignee_user_id": value})

    assert response.status_code == 422
    assert response.json()["detail"] == triage.ASSIGNEE_INELIGIBLE
    assert world.defect.assignee_user_id is None
    assert _ledger(world.db) == []


async def test_an_admin_who_is_not_a_member_can_be_assigned(world):
    admin = user(UserRole.ADMIN, name="Ada Admin")
    world.db.rows.append(admin)

    response = await world.patch({"assignee_user_id": str(admin.id)})

    assert response.status_code == 200, response.text
    assert world.defect.assignee_user_id == admin.id
    assert response.json()["changed_fields"] == ["assignee"]


async def test_assign_records_names_and_null_clears(world):
    response = await world.patch({"assignee_user_id": str(world.priya.id), "note": "taking it"})
    assert response.status_code == 200, response.text
    assert world.defect.assignee_user_id == world.priya.id

    (row,) = _ledger(world.db)
    assert row.event_type == "defect.updated"
    assert row.diff["before"] == {"assignee": "Unassigned"}
    assert row.diff["after"] == {"assignee": "Priya Nair"}
    assert row.context["note"] == "taking it"
    assert "assignee → Priya Nair" in row.summary
    # Names, never ids, anywhere in the diff.
    assert str(world.priya.id) not in json.dumps(row.diff)

    cleared = await world.patch({"assignee_user_id": None})
    assert cleared.status_code == 200
    assert world.defect.assignee_user_id is None
    assert cleared.json()["changed_fields"] == ["assignee"]


async def test_resending_the_current_assignee_records_nothing(world):
    world.defect.assignee_user_id = world.priya.id

    response = await world.patch({"assignee_user_id": str(world.priya.id)})

    assert response.status_code == 200
    assert response.json()["changed_fields"] == []
    assert _ledger(world.db) == []
    assert world.bumps == []


async def test_owner_team_trims_and_unknown_clears(world):
    response = await world.patch({"owner_team": "  Payments  "})
    assert response.status_code == 200
    assert world.defect.owner_team == "Payments"
    (row,) = _ledger(world.db)
    assert row.diff["after"] == {"owner_team": "Payments"}

    cleared = await world.patch({"owner_team": "unknown"})
    assert cleared.json()["changed_fields"] == ["owner_team"]
    assert world.defect.owner_team is None


# ── 4. re-open conflicts (D-3) ─────────────────────────────────────────────


def _twin(world, status: str):
    twin = defect(PID, test_case_id=world.test_case, resolution_status=status, jira_ticket_id="QA-77")
    world.db.rows.append(twin)
    return twin


async def test_reopening_onto_an_open_twin_is_409(world):
    world.defect.resolution_status = "RESOLVED"
    twin = _twin(world, "OPEN")

    response = await world.patch({"resolution_status": "OPEN"})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "defect_already_open"
    assert detail["defect_id"] == str(twin.id)
    assert "(QA-77)" in detail["message"]
    assert world.defect.resolution_status == "RESOLVED"
    assert _ledger(world.db) == []


async def test_reopening_into_in_progress_with_a_live_twin_is_409(world):
    world.defect.resolution_status = "RESOLVED"
    _twin(world, "IN_PROGRESS")

    response = await world.patch({"resolution_status": "IN_PROGRESS"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "defect_already_open"
    assert world.defect.resolution_status == "RESOLVED"


async def test_open_to_in_progress_beside_a_legacy_in_progress_twin_is_allowed(world):
    _twin(world, "IN_PROGRESS")

    response = await world.patch({"resolution_status": "IN_PROGRESS"})

    assert response.status_code == 200, response.text
    assert world.defect.resolution_status == "IN_PROGRESS"


async def test_a_twin_in_another_project_is_not_a_conflict(world):
    world.defect.resolution_status = "RESOLVED"
    world.db.rows.append(defect(OTHER_PID, test_case_id=world.test_case, resolution_status="OPEN"))

    response = await world.patch({"resolution_status": "OPEN"})

    assert response.status_code == 200, response.text


async def test_the_unique_index_backstop_answers_the_same_409(world):
    """A concurrent second OPEN that slipped past the check: the flush raises
    on ``ix_defects_test_case_open_unique``; the route rolls back and answers
    409, and nothing staged survives (the ledger row dies with the rollback)."""
    world.defect.resolution_status = "RESOLVED"
    world.db.fail_flush_with = IntegrityError(
        "UPDATE defects", {}, Exception(
            'duplicate key value violates unique constraint "ix_defects_test_case_open_unique"'
        ),
    )

    response = await world.patch({"resolution_status": "OPEN"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "defect_already_open"
    assert "rollback" in world.db.journal
    assert "commit" not in world.db.journal
    assert _ledger(world.db) == []
    assert world.bumps == []


async def test_another_integrity_error_is_not_swallowed(world):
    world.db.fail_flush_with = IntegrityError("UPDATE defects", {}, Exception("some other constraint"))

    response = await world.patch({"resolution_status": "RESOLVED"})

    assert response.status_code == 500


# ── 9. the ledger ──────────────────────────────────────────────────────────


async def test_a_status_change_records_labels_not_words(world):
    response = await world.patch({"resolution_status": "IN_PROGRESS", "note": "on it"})

    assert response.status_code == 200
    (row,) = _ledger(world.db)
    assert row.event_type == "defect.updated"
    assert row.entity_type == "defect" and row.entity_id == str(world.defect.id)
    assert row.summary == "Defect Checkout total off by a cent changed: status → In progress"
    assert row.diff["before"] == {"status": "Open"}
    assert row.diff["after"] == {"status": "In progress"}
    assert row.diff["changed_fields"] == ["resolution_status"]
    assert row.context == {"changed": "status → In progress", "jira_write_back": None, "note": "on it"}
    assert row.actor_id == world.engineer.id


async def test_a_link_records_ticket_linked_with_its_validation(world):
    response = await world.patch({"jira_key": "abc-123"})

    assert response.status_code == 200, response.text
    (row,) = _ledger(world.db)
    assert row.event_type == "defect.ticket_linked"
    assert row.context == {"ticket_key": "ABC-123", "validated": False}
    assert row.summary == "Defect Checkout total off by a cent linked to Jira ABC-123"


async def test_unlink_is_an_update(world):
    world.defect.jira_ticket_id = "QA-9"
    world.defect.jira_ticket_url = "https://acme.atlassian.net/browse/QA-9"
    world.defect.jira_status = "In Progress"

    response = await world.patch({"jira_key": None})

    assert response.status_code == 200
    assert response.json()["jira"]["status"] == "unlinked"
    assert world.defect.jira_ticket_id is None and world.defect.jira_ticket_url is None
    assert world.defect.jira_status is None
    (row,) = _ledger(world.db)
    assert row.event_type == "defect.updated"
    assert row.context["changed"] == "Jira link removed"
    assert row.diff["changed_fields"] == ["jira"]


async def test_a_mixed_patch_reports_every_change_in_order(world):
    response = await world.patch({
        "jira_key": "QA-5", "owner_team": "Payments", "resolution_status": "IN_PROGRESS",
        "assignee_user_id": str(world.priya.id),
    })

    assert response.status_code == 200, response.text
    assert response.json()["changed_fields"] == ["resolution_status", "assignee", "owner_team", "jira"]
    events = sorted(r.event_type for r in _ledger(world.db))
    assert events == ["defect.ticket_linked", "defect.updated"]
    (updated,) = [r for r in _ledger(world.db) if r.event_type == "defect.updated"]
    assert updated.context["changed"] == (
        "status → In progress, assignee → Priya Nair, owner team → Payments"
    )
    # Linked + offline: the write-back is recorded as not sent, with the reason.
    assert updated.context["jira_write_back"] == "not_sent:offline_mode"
    assert response.json()["write_back"] == {"status": "not_sent", "reason": "offline_mode"}
    assert world.http.requests == []
