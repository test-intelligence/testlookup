"""W28 TCM-04: ``POST /api/v1/test-management/cases/{case_id}/restore``.

Restoring copies an older version's content and classification
(``RESTORABLE_FIELDS``) into a NEW version through the edit path, so the
stale-version 409 is the PATCH's exact body and the edit path's rules apply.
Status, owner and fingerprint are never restored. Nothing differing writes
nothing. The service tests drive the REAL ``update_managed_test_case`` through
the bulk tests' session fake.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.models.postgres import ProjectActivityEvent, TestCaseAuditLog as AuditRow, TestCaseVersion as VersionRow
from app.models.schemas import TestCaseRestoreRequest as RestoreRequest
from app.services.test_case_editing_service import RESTORABLE_FIELDS, restore_case_version
from tests.test_test_case_bulk import PROJECT, FakeSession, actor, make_case


def snapshot(case, number: int, **fields) -> VersionRow:
    base: dict[str, Any] = {
        "title": case.title, "description": None, "objective": None, "preconditions": None,
        "steps": None, "parameters": None, "expected_result": None, "test_data": None,
        "test_type": "functional", "priority": "medium", "severity": "major", "feature_area": None,
        "suite_name": None, "tags": ["smoke"], "estimated_duration_minutes": None,
        "is_automated": False, "automation_status": "not_automated",
    }
    base.update(fields)
    return VersionRow(
        id=uuid.uuid4(), test_case_id=case.id, version=number, status=fields.pop("status", "draft"),
        change_type="updated", test_fingerprint="fp-old",
        **{k: v for k, v in base.items() if k != "status"},
    )


def body(version: int, expected: int, summary: str | None = None) -> RestoreRequest:
    return RestoreRequest(version=version, expected_version=expected, change_summary=summary)


def test_restorable_fields_never_include_status_owner_or_fingerprint():
    assert "status" not in RESTORABLE_FIELDS
    assert "assignee_id" not in RESTORABLE_FIELDS
    assert "test_fingerprint" not in RESTORABLE_FIELDS
    assert RESTORABLE_FIELDS == (
        "title", "description", "objective", "preconditions", "steps", "parameters", "expected_result",
        "test_data", "test_type", "priority", "severity", "feature_area", "suite_name", "tags",
        "estimated_duration_minutes", "is_automated", "automation_status",
    )


@pytest.mark.asyncio
async def test_restoring_v2_of_a_v4_case_saves_v5_with_v2_s_content():
    owner = uuid.uuid4()
    case = make_case(
        "active", version=4, title="Pay by card (new flow)", priority="high", tags=["smoke", "new"],
        steps=[{"step_number": 1, "action": "Open checkout (new)", "expected_result": None}],
        assignee_id=owner, test_fingerprint="fp-now",
    )
    old_steps = [
        {"step_number": 1, "action": "Open checkout", "expected_result": "Checkout shows"},
        {"step_number": 2, "action": "Pay by card", "expected_result": "Paid"},
    ]
    v2 = snapshot(case, 2, title="Pay by card", priority="medium", tags=["smoke"], steps=old_steps, status="draft")
    db = FakeSession([case], versions=[v2])
    user = actor("TESTER", full_name="Tess Tester")

    restored = await restore_case_version(db, case, body(2, 4), user)

    assert restored is case
    assert (case.version, case.title, case.priority, case.tags) == (5, "Pay by card", "medium", ["smoke"])
    assert case.steps == old_steps
    # Never restored: status, owner, fingerprint.
    assert (case.status, case.assignee_id, case.test_fingerprint) == ("active", owner, "fp-now")
    (version,) = db.of(VersionRow)
    assert (version.version, version.change_type, version.change_summary) == (5, "restored", "Restored from v2")
    assert version.changed_fields == ["priority", "steps", "tags", "title"]
    assert version.status == "active"
    (audit,) = db.of(AuditRow)
    assert audit.action == "updated" and audit.old_values["version"] == 4
    (event,) = db.of(ProjectActivityEvent)
    assert event.event_type == "test_case.restored"
    assert (event.entity_type, event.entity_id, event.entity_label) == ("test_case", str(case.id), "Pay by card")
    assert event.project_id == PROJECT
    assert event.context == {"from_version": 2, "to_version": 5}
    assert event.diff == {"changed_fields": ["priority", "steps", "tags", "title"]}
    assert event.summary == "Test case Pay by card restored from v2"


@pytest.mark.asyncio
async def test_the_reader_s_change_summary_is_kept():
    case = make_case("draft", version=3, title="Now")
    db = FakeSession([case], versions=[snapshot(case, 1, title="Then")])
    await restore_case_version(db, case, body(1, 3, "Back to the reviewed wording"), actor())
    assert db.of(VersionRow)[0].change_summary == "Back to the reviewed wording"


@pytest.mark.asyncio
async def test_an_unknown_version_is_404():
    case = make_case("draft", version=3)
    db = FakeSession([case], versions=[])
    with pytest.raises(HTTPException) as exc:
        await restore_case_version(db, case, body(2, 3), actor())
    assert (exc.value.status_code, exc.value.detail) == (404, "Version not found")
    assert db.added == []


@pytest.mark.asyncio
async def test_the_current_version_is_422_and_reads_nothing():
    case = make_case("draft", version=3)
    db = FakeSession([case], versions=[snapshot(case, 3)])
    with pytest.raises(HTTPException) as exc:
        await restore_case_version(db, case, body(3, 3), actor())
    assert (exc.value.status_code, exc.value.detail) == (422, "v3 is the current version")
    assert db.statements == [] and db.added == []


@pytest.mark.asyncio
async def test_a_stale_expected_version_is_the_patch_s_exact_409():
    case = make_case("draft", version=4, title="Now")
    db = FakeSession([case], versions=[snapshot(case, 2, title="Then")])
    with pytest.raises(HTTPException) as exc:
        await restore_case_version(db, case, body(2, 3), actor())
    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "current_version": 4,
        "expected_version": 3,
        "message": "Test case changed; refresh before saving edits",
    }
    assert case.title == "Now" and db.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["deprecated", "archived"])
async def test_a_retired_case_is_the_edit_path_s_409(status):
    case = make_case(status, version=4, title="Now")
    db = FakeSession([case], versions=[snapshot(case, 2, title="Then")])
    with pytest.raises(HTTPException) as exc:
        await restore_case_version(db, case, body(2, 4), actor())
    assert (exc.value.status_code, exc.value.detail) == (409, f"Cannot edit a {status} test case")
    assert db.added == []


@pytest.mark.asyncio
async def test_equal_content_returns_the_case_unchanged_with_no_version_and_no_activity():
    steps = [{"step_number": 1, "action": "Open", "expected_result": None}]
    case = make_case("active", version=4, title="Same", steps=steps, tags=["b", "a"])
    # The same content, stored with other key orders: compared as normalised JSON.
    v2 = snapshot(case, 2, title="Same", steps=[{"expected_result": None, "action": "Open", "step_number": 1}], tags=["b", "a"])
    db = FakeSession([case], versions=[v2])
    with patch("app.services.test_case_editing_service.update_managed_test_case", AsyncMock()) as edit:
        same = await restore_case_version(db, case, body(2, 4), actor())
    assert same is case and case.version == 4
    edit.assert_not_awaited()
    assert db.added == []


@pytest.mark.asyncio
async def test_an_old_snapshot_that_no_longer_validates_is_422_with_the_sentence():
    case = make_case("draft", version=3, title="Now")
    v1 = snapshot(case, 1, title="Then", steps=[{"step_number": 1, "action": "", "expected_result": None}])
    db = FakeSession([case], versions=[v1])
    with pytest.raises(HTTPException) as exc:
        await restore_case_version(db, case, body(1, 3), actor())
    assert exc.value.status_code == 422
    assert exc.value.detail == "v1 cannot be restored: steps.0.action: String should have at least 1 character"
    assert case.title == "Now" and db.added == []


@pytest.mark.asyncio
async def test_a_value_an_old_snapshot_did_not_record_is_left_as_it_is():
    """A NOT NULL column (test_type here) that a snapshot stored as NULL is not "restored" to NULL."""
    case = make_case("draft", version=3, title="Now")
    v1 = snapshot(case, 1, title="Then", test_type=None, priority=None)
    db = FakeSession([case], versions=[v1])
    await restore_case_version(db, case, body(1, 3), actor())
    assert (case.title, case.test_type, case.priority) == ("Then", "functional", "medium")
    assert db.of(VersionRow)[0].changed_fields == ["title"]


@pytest.mark.asyncio
async def test_a_masked_parameter_s_stored_value_comes_back_and_never_reaches_the_ledger():
    case = make_case(
        "draft", version=3,
        parameters=[{"name": "password", "value": "new-secret", "mode": None, "masked": True}],
    )
    v1 = snapshot(case, 1, parameters=[{"name": "password", "value": "old-secret", "mode": None, "masked": True}])
    db = FakeSession([case], versions=[v1])
    await restore_case_version(db, case, body(1, 3), actor())
    assert case.parameters == [{"name": "password", "value": "old-secret", "mode": None, "masked": True}]
    event = db.of(ProjectActivityEvent)[0]
    assert "secret" not in repr(event.context) and "secret" not in repr(event.diff)
    audit = db.of(AuditRow)[0]
    assert "old-secret" not in repr(audit.new_values)


# ═══════════════════════ the route ═══════════════════════


@pytest.fixture
def restore_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import test_management, test_management_editing as route_mod
    from app.routers.test_management_shared import require_case_access

    case = make_case("active", version=4)
    app = FastAPI()
    app.include_router(test_management.router)
    db = AsyncMock()

    async def _db():
        yield db

    state: dict[str, Any] = {"user": actor("TESTER")}
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state["user"]
    app.dependency_overrides[require_case_access] = lambda: case
    with (
        patch.object(route_mod, "restore_case_version", AsyncMock(return_value=case)) as service,
        patch.object(route_mod, "case_response", AsyncMock(return_value={
            "id": str(case.id), "project_id": str(PROJECT), "title": case.title, "test_type": "functional",
            "priority": "medium", "severity": "major", "status": "active", "version": 5,
            "is_automated": False, "automation_status": "not_automated", "ai_generated": False,
            "created_at": "2026-10-18T00:00:00Z", "updated_at": "2026-10-18T00:00:00Z",
        })) as respond,
        patch.object(route_mod, "emit_staged_test_management_metrics", AsyncMock()),
    ):
        yield SimpleNamespace(
            client=TestClient(app, raise_server_exceptions=False), state=state, db=db,
            service=service, respond=respond, case=case,
        )


def test_the_restore_route_restores_commits_and_answers_the_case(restore_client):
    case = restore_client.case
    resp = restore_client.client.post(
        f"/api/v1/test-management/cases/{case.id}/restore", json={"version": 2, "expected_version": 4},
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 5
    args = restore_client.service.await_args.args
    assert args[1] is case and (args[2].version, args[2].expected_version) == (2, 4)
    assert restore_client.db.commit.await_count == 1
    assert restore_client.respond.await_args.args[1] is case


def test_a_viewer_cannot_restore(restore_client):
    restore_client.state["user"] = actor("VIEWER")
    case = restore_client.case
    resp = restore_client.client.post(
        f"/api/v1/test-management/cases/{case.id}/restore", json={"version": 2, "expected_version": 4},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Requires at least TESTER role"
    restore_client.service.assert_not_awaited()


@pytest.mark.parametrize("bad", [{"version": 0, "expected_version": 4}, {"version": 2}, {"expected_version": 4}])
def test_a_malformed_restore_body_is_422(restore_client, bad):
    case = restore_client.case
    resp = restore_client.client.post(f"/api/v1/test-management/cases/{case.id}/restore", json=bad)
    assert resp.status_code == 422
    restore_client.service.assert_not_awaited()


def test_the_restore_route_depends_on_require_case_access():
    from app.routers import test_management_editing as route_mod
    from app.routers.test_management_shared import require_case_access

    route = next(r for r in route_mod.router.routes if r.path == "/cases/{case_id}/restore")
    assert require_case_access in [d.call for d in route.dependant.dependencies]
