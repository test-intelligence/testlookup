"""API integration tests for /api/v1/suites + /api/v1/canonical-test-cases.

Covers the HTTP surface that the service-layer tests (``test_test_suite_service.py``)
don't reach: auth gates, 4xx error mapping, cross-project-move refusal,
and project-access enforcement.

Service-level happy-paths and SQL behaviour are already pinned by
``test_test_suite_service.py``. This file deliberately stubs the service
layer so failures here point at router wiring, schema validation, or
auth — not at SQL semantics.

Phase I follow-up (`docs/BACKLOG.md` Phase I #4).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jose")
pytest.importorskip("asyncpg")

from fastapi import HTTPException  # noqa: E402

from app.models.postgres import UserRole  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _patch_router_imported_accessible(monkeypatch):
    """Patch the router's *local* import of ``get_accessible_project_ids``.

    ``routers/suites.py`` does ``from app.core.deps import
    get_accessible_project_ids`` at module load, binding the function to
    a local name. The shared ``auth_as`` fixture monkeypatches
    ``app.core.deps.get_accessible_project_ids`` and installs a
    dependency override for the FastAPI DI path, but the router calls
    the function directly (not via DI) using its pre-bound local name —
    so neither hook reaches it. Every test that goes through
    ``_enforce_project_access`` times out trying to query the (mocked)
    session and returns 403.

    Here we delegate to whatever ``auth_as`` installed under
    ``app.dependency_overrides[get_accessible_project_ids]``. That keeps
    a single source of truth for the accessible-set across the conftest
    and this file, and stays a no-op when no auth override is active.
    """
    from app.core.deps import get_accessible_project_ids as real
    from app.main import app
    from app.routers import suites as suites_router

    async def _resolve(db, user):
        override = app.dependency_overrides.get(real)
        if override is not None:
            return await override(db, user)
        return await real(db, user)

    monkeypatch.setattr(suites_router, "get_accessible_project_ids", _resolve)
    yield


# ── Fixtures local to this file ───────────────────────────────────────────────


def _suite_obj(**kwargs) -> SimpleNamespace:
    """Build a SimpleNamespace with the attributes a TestSuite row carries
    so ``TestSuiteResponse.model_validate({**suite.__dict__, ...})`` works."""
    now = datetime.now(timezone.utc)
    defaults = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "name": "Sample Suite",
        "description": None,
        "tags": None,
        "is_default": False,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _canonical_obj(**kwargs) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    defaults = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "test_suite_id": uuid.uuid4(),
        "test_fingerprint": "fp-abc",
        "test_name": "test_login",
        "class_name": "tests.smoke.LoginTests",
        "status": "active",
        "source": "execution",
        "first_seen_run_id": None,
        "last_seen_run_id": None,
        "last_seen_test_case_id": None,
        "deleted_at_run_id": None,
        "managed_test_case_id": None,
        "retirement_confirmed_at": None,
        "retirement_confirmed_by_id": None,
        "retirement_reason": None,
        "deleted_observed_at": None,
        "review_tag": None,
        "tags": None,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _managed_obj(**kwargs) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    defaults = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "title": "test_login",
        "description": None,
        "objective": None,
        "preconditions": None,
        "steps": None,
        "parameters": None,
        "expected_result": None,
        "test_data": None,
        "test_type": "automation",
        "priority": "medium",
        "severity": "major",
        "feature_area": "LoginTests",
        "suite_name": "Smoke",
        "test_suite_id": uuid.uuid4(),
        "tags": None,
        "status": "draft",
        "version": 1,
        "lifecycle_state_changed_at": now,
        "approved_at": None,
        "approved_by_id": None,
        "needs_update_reason": None,
        "deprecation_reason": None,
        "deprecated_at": None,
        "deprecated_by_id": None,
        "archived_at": None,
        "archived_by_id": None,
        "author_id": uuid.uuid4(),
        "assignee_id": None,
        "reviewer_id": None,
        "is_automated": True,
        "automation_status": "automated",
        "test_fingerprint": "fp-abc",
        "ai_generated": False,
        "ai_quality_score": None,
        "ai_review_notes": None,
        "estimated_duration_minutes": None,
        "last_executed_at": None,
        "last_execution_status": None,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ── /api/v1/suites — list ─────────────────────────────────────────────────────


async def test_list_suites_returns_only_accessible_projects(client, auth_as):
    """A caller without membership in the requested project gets 403; with
    membership, the service receives the scoped project list."""
    project_a = uuid.uuid4()
    project_b = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_a})

    # Asking for project B — caller has no membership → 403.
    resp = await client.get(f"/api/v1/suites?project_id={project_b}")
    assert resp.status_code == 403

    # Asking for project A — service is called with [project_a].
    with patch(
        "app.services.test_suite_service.list_test_suites",
        AsyncMock(return_value=[]),
    ) as mock_list:
        resp = await client.get(f"/api/v1/suites?project_id={project_a}")
    assert resp.status_code == 200, resp.text
    # Service was invoked with the project-scoped list (positional arg).
    _, args, _ = mock_list.mock_calls[0]
    assert args[1] == [project_a]


async def test_list_suites_admin_sees_unfiltered(client, auth_as):
    """ADMIN role → service is called with project_ids=None so it scans
    every project."""
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.test_suite_service.list_test_suites",
        AsyncMock(return_value=[]),
    ) as mock_list:
        resp = await client.get("/api/v1/suites")
    assert resp.status_code == 200
    _, args, _ = mock_list.mock_calls[0]
    assert args[1] is None


# ── /api/v1/suites — create ───────────────────────────────────────────────────


async def test_create_suite_requires_qa_engineer(client, auth_as):
    """VIEWER and TESTER are below QA_ENGINEER → 403."""
    project_id = uuid.uuid4()
    auth_as(role=UserRole.VIEWER, accessible_projects={project_id})
    resp = await client.post(
        "/api/v1/suites",
        json={"project_id": str(project_id), "name": "Smoke"},
    )
    assert resp.status_code == 403


async def test_create_suite_refuses_inaccessible_project(client, auth_as):
    """Even a QA_ENGINEER can't create a suite in a project they aren't a
    member of — the router-level access check fires before the service."""
    project_caller_can_see = uuid.uuid4()
    project_caller_targets = uuid.uuid4()
    auth_as(
        role=UserRole.QA_ENGINEER,
        accessible_projects={project_caller_can_see},
    )
    resp = await client.post(
        "/api/v1/suites",
        json={"project_id": str(project_caller_targets), "name": "Smoke"},
    )
    assert resp.status_code == 403


async def test_create_suite_translates_409_from_service(client, auth_as):
    """Name collision in the service surfaces as 409 to the HTTP caller."""
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    conflict = HTTPException(status_code=409, detail="duplicate")
    with patch(
        "app.services.test_suite_service.create_test_suite",
        AsyncMock(side_effect=conflict),
    ):
        resp = await client.post(
            "/api/v1/suites",
            json={"project_id": str(project_id), "name": "Smoke"},
        )
    assert resp.status_code == 409


async def test_create_suite_happy_path(client, auth_as):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    created = _suite_obj(project_id=project_id, name="Smoke")
    with patch(
        "app.services.test_suite_service.create_test_suite",
        AsyncMock(return_value=created),
    ):
        resp = await client.post(
            "/api/v1/suites",
            json={"project_id": str(project_id), "name": "Smoke"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Smoke"
    assert body["project_id"] == str(project_id)


# ── /api/v1/suites/{id} — get / update / delete / set-default ─────────────────


async def test_update_suite_requires_qa_engineer(client, auth_as):
    project_id = uuid.uuid4()
    suite = _suite_obj(project_id=project_id)
    auth_as(role=UserRole.TESTER, accessible_projects={project_id})
    # Below QA_ENGINEER → 403 even before the suite is loaded.
    resp = await client.patch(
        f"/api/v1/suites/{suite.id}",
        json={"name": "Renamed"},
    )
    assert resp.status_code == 403


async def test_update_suite_404_when_missing(client, auth_as):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    not_found = HTTPException(status_code=404, detail="Test suite not found")
    with patch(
        "app.services.test_suite_service.get_suite_or_404",
        AsyncMock(side_effect=not_found),
    ):
        resp = await client.patch(
            f"/api/v1/suites/{uuid.uuid4()}",
            json={"name": "Renamed"},
        )
    assert resp.status_code == 404


async def test_update_suite_translates_409_from_service(client, auth_as):
    project_id = uuid.uuid4()
    suite = _suite_obj(project_id=project_id)
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    conflict = HTTPException(status_code=409, detail="name taken")
    with patch(
        "app.services.test_suite_service.get_suite_or_404",
        AsyncMock(return_value=suite),
    ), patch(
        "app.services.test_suite_service.update_test_suite",
        AsyncMock(side_effect=conflict),
    ):
        resp = await client.patch(
            f"/api/v1/suites/{suite.id}",
            json={"name": "ClashingName"},
        )
    assert resp.status_code == 409


async def test_delete_suite_requires_qa_lead(client, auth_as):
    """The delete endpoint is gated at QA_LEAD — QA_ENGINEER is not enough."""
    project_id = uuid.uuid4()
    suite_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    resp = await client.delete(f"/api/v1/suites/{suite_id}")
    assert resp.status_code == 403


async def test_delete_default_suite_is_refused(client, auth_as):
    """The service raises 400 when the default suite is targeted — the
    router must propagate it unchanged."""
    project_id = uuid.uuid4()
    suite = _suite_obj(project_id=project_id, is_default=True)
    auth_as(role=UserRole.QA_LEAD, accessible_projects={project_id})
    with patch(
        "app.services.test_suite_service.get_suite_or_404",
        AsyncMock(return_value=suite),
    ), patch(
        "app.services.test_suite_service.delete_test_suite",
        AsyncMock(side_effect=HTTPException(
            status_code=400,
            detail="The default suite cannot be deleted; rename or move cases first",
        )),
    ):
        resp = await client.delete(f"/api/v1/suites/{suite.id}")
    assert resp.status_code == 400


async def test_delete_non_empty_suite_returns_409(client, auth_as):
    project_id = uuid.uuid4()
    suite = _suite_obj(project_id=project_id)
    auth_as(role=UserRole.QA_LEAD, accessible_projects={project_id})
    with patch(
        "app.services.test_suite_service.get_suite_or_404",
        AsyncMock(return_value=suite),
    ), patch(
        "app.services.test_suite_service.delete_test_suite",
        AsyncMock(side_effect=HTTPException(
            status_code=409,
            detail="Suite still has 3 test case(s); move or delete them first",
        )),
    ):
        resp = await client.delete(f"/api/v1/suites/{suite.id}")
    assert resp.status_code == 409


async def test_delete_suite_refuses_inaccessible_project(client, auth_as):
    """Even with QA_LEAD, a delete against a non-accessible project returns 403."""
    caller_project = uuid.uuid4()
    other_project = uuid.uuid4()
    suite = _suite_obj(project_id=other_project)
    auth_as(role=UserRole.QA_LEAD, accessible_projects={caller_project})
    with patch(
        "app.services.test_suite_service.get_suite_or_404",
        AsyncMock(return_value=suite),
    ):
        resp = await client.delete(f"/api/v1/suites/{suite.id}")
    assert resp.status_code == 403


async def test_delete_suite_happy_path(client, auth_as):
    project_id = uuid.uuid4()
    suite = _suite_obj(project_id=project_id)
    auth_as(role=UserRole.QA_LEAD, accessible_projects={project_id})
    with patch(
        "app.services.test_suite_service.get_suite_or_404",
        AsyncMock(return_value=suite),
    ), patch(
        "app.services.test_suite_service.delete_test_suite",
        AsyncMock(return_value=None),
    ) as mock_delete:
        resp = await client.delete(f"/api/v1/suites/{suite.id}")
    assert resp.status_code == 204
    mock_delete.assert_awaited_once()


async def test_set_default_requires_qa_lead(client, auth_as):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    resp = await client.post(f"/api/v1/suites/{uuid.uuid4()}/set-default")
    assert resp.status_code == 403


async def test_set_default_happy_path(client, auth_as):
    project_id = uuid.uuid4()
    suite = _suite_obj(project_id=project_id, is_default=False)
    auth_as(role=UserRole.QA_LEAD, accessible_projects={project_id})

    async def _promote(_db, s):
        s.is_default = True
        return s

    with patch(
        "app.services.test_suite_service.get_suite_or_404",
        AsyncMock(return_value=suite),
    ), patch(
        "app.services.test_suite_service.set_default_suite",
        AsyncMock(side_effect=_promote),
    ):
        resp = await client.post(f"/api/v1/suites/{suite.id}/set-default")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_default"] is True


# ── /api/v1/canonical-test-cases/{id}/link — cross-project refusal ────────────


async def test_link_requires_qa_engineer(client, auth_as):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.VIEWER, accessible_projects={project_id})
    resp = await client.post(
        f"/api/v1/canonical-test-cases/{uuid.uuid4()}/link",
        json={"test_suite_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 403


async def test_link_refuses_cross_project_move(client, auth_as):
    """The service raises 400 when the target suite is in a different
    project; the router must propagate that verbatim — otherwise the UI
    would silently move a canonical across tenant boundaries."""
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    canonical = _canonical_obj(project_id=project_id)
    target_suite = _suite_obj(project_id=uuid.uuid4())  # different project

    with patch(
        "app.services.test_suite_service.get_canonical_or_404",
        AsyncMock(return_value=canonical),
    ), patch(
        "app.services.test_suite_service.get_suite_or_404",
        AsyncMock(return_value=target_suite),
    ), patch(
        "app.services.test_suite_service.link_canonical_to_suite",
        AsyncMock(side_effect=HTTPException(
            status_code=400,
            detail="Cannot move a test case to a suite in a different project",
        )),
    ):
        resp = await client.post(
            f"/api/v1/canonical-test-cases/{canonical.id}/link",
            json={"test_suite_id": str(target_suite.id)},
        )
    assert resp.status_code == 400
    assert "different project" in resp.json()["detail"]


async def test_link_happy_path_within_project(client, auth_as):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    canonical = _canonical_obj(project_id=project_id)
    target_suite = _suite_obj(project_id=project_id, name="Renamed Target")

    async def _link(_db, c, t):
        c.test_suite_id = t.id
        return c

    with patch(
        "app.services.test_suite_service.get_canonical_or_404",
        AsyncMock(return_value=canonical),
    ), patch(
        "app.services.test_suite_service.get_suite_or_404",
        AsyncMock(return_value=target_suite),
    ), patch(
        "app.services.test_suite_service.link_canonical_to_suite",
        AsyncMock(side_effect=_link),
    ):
        resp = await client.post(
            f"/api/v1/canonical-test-cases/{canonical.id}/link",
            json={"test_suite_id": str(target_suite.id)},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["test_suite_id"] == str(target_suite.id)


# ── /api/v1/canonical-test-cases/bulk-link ────────────────────────────────────


async def test_bulk_link_requires_qa_engineer(client, auth_as):
    """VIEWER + TESTER are below QA_ENGINEER → 403 before the body is
    parsed (same as the single-id link endpoint)."""
    project_id = uuid.uuid4()
    auth_as(role=UserRole.VIEWER, accessible_projects={project_id})
    resp = await client.post(
        "/api/v1/canonical-test-cases/bulk-link",
        json={
            "target_test_suite_id": str(uuid.uuid4()),
            "canonical_ids": [str(uuid.uuid4())],
        },
    )
    assert resp.status_code == 403


async def test_bulk_link_refuses_inaccessible_target_project(client, auth_as):
    """The target suite's project must be accessible to the caller —
    otherwise an attacker could pin a target in their own project and
    use the body to enumerate other projects' canonical ids."""
    caller_project = uuid.uuid4()
    target_project = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={caller_project})
    target_suite = _suite_obj(project_id=target_project)

    with patch(
        "app.services.test_suite_service.get_suite_or_404",
        AsyncMock(return_value=target_suite),
    ):
        resp = await client.post(
            "/api/v1/canonical-test-cases/bulk-link",
            json={
                "target_test_suite_id": str(target_suite.id),
                "canonical_ids": [str(uuid.uuid4())],
            },
        )
    assert resp.status_code == 403


async def test_bulk_link_404_when_target_suite_missing(client, auth_as):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})

    not_found = HTTPException(status_code=404, detail="Test suite not found")
    with patch(
        "app.services.test_suite_service.get_suite_or_404",
        AsyncMock(side_effect=not_found),
    ):
        resp = await client.post(
            "/api/v1/canonical-test-cases/bulk-link",
            json={
                "target_test_suite_id": str(uuid.uuid4()),
                "canonical_ids": [str(uuid.uuid4())],
            },
        )
    assert resp.status_code == 404


async def test_bulk_link_propagates_400_on_cross_project_id(client, auth_as):
    """Service raises 400 on any cross-project id; router must propagate."""
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    target_suite = _suite_obj(project_id=project_id)

    cross_project_400 = HTTPException(
        status_code=400,
        detail="Cannot move test cases to a suite in a different project (1 of 2 ids are from other projects)",
    )
    with patch(
        "app.services.test_suite_service.get_suite_or_404",
        AsyncMock(return_value=target_suite),
    ), patch(
        "app.services.test_suite_service.bulk_link_canonicals_to_suite",
        AsyncMock(side_effect=cross_project_400),
    ):
        resp = await client.post(
            "/api/v1/canonical-test-cases/bulk-link",
            json={
                "target_test_suite_id": str(target_suite.id),
                "canonical_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
            },
        )
    assert resp.status_code == 400
    assert "different project" in resp.json()["detail"]


async def test_bulk_link_empty_body_is_422(client, auth_as):
    """Schema requires ``min_length=1`` on ``canonical_ids`` — a no-op
    POST is rejected by FastAPI's validation, never reaching the
    handler. Saves the round trip on a UI mishap."""
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    resp = await client.post(
        "/api/v1/canonical-test-cases/bulk-link",
        json={
            "target_test_suite_id": str(uuid.uuid4()),
            "canonical_ids": [],
        },
    )
    assert resp.status_code == 422


async def test_bulk_link_happy_path_returns_counts(client, auth_as):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    target_suite = _suite_obj(project_id=project_id)
    canonical_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]

    with patch(
        "app.services.test_suite_service.get_suite_or_404",
        AsyncMock(return_value=target_suite),
    ), patch(
        "app.services.test_suite_service.bulk_link_canonicals_to_suite",
        AsyncMock(return_value={
            "moved": 2,
            "skipped_already_in_target": 1,
            "missing_ids": [],
        }),
    ):
        resp = await client.post(
            "/api/v1/canonical-test-cases/bulk-link",
            json={
                "target_test_suite_id": str(target_suite.id),
                "canonical_ids": [str(cid) for cid in canonical_ids],
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["moved"] == 2
    assert body["skipped_already_in_target"] == 1
    assert body["missing_ids"] == []


# ── S-P canonical lifecycle governance ───────────────────────────────────────


async def test_orphaned_list_rejects_inaccessible_project_before_query(
    client, auth_as
):
    own_project = uuid.uuid4()
    foreign_project = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={own_project})

    with patch(
        "app.services.test_suite_service.list_orphaned_canonical_cases",
        AsyncMock(return_value=([], 0)),
    ) as list_orphans:
        response = await client.get(
            f"/api/v1/canonical-test-cases/orphaned?project_id={foreign_project}"
        )

    assert response.status_code == 403
    list_orphans.assert_not_awaited()


async def test_orphaned_list_passes_only_the_callers_project_scope(
    client, auth_as
):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})

    with patch(
        "app.services.test_suite_service.list_orphaned_canonical_cases",
        AsyncMock(return_value=([], 0)),
    ) as list_orphans:
        response = await client.get(
            f"/api/v1/canonical-test-cases/orphaned?project_id={project_id}"
            "&page=3&size=17"
        )

    assert response.status_code == 200, response.text
    list_orphans.assert_awaited_once()
    assert list_orphans.await_args.args[1] == [project_id]
    assert list_orphans.await_args.kwargs == {"page": 3, "size": 17}


async def test_promote_requires_qa_engineer(client, auth_as):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.VIEWER, accessible_projects={project_id})
    response = await client.post(
        f"/api/v1/canonical-test-cases/{uuid.uuid4()}/promote"
    )
    assert response.status_code == 403


async def test_promote_returns_exact_canonical_and_managed_shape(client, auth_as):
    project_id = uuid.uuid4()
    canonical = _canonical_obj(project_id=project_id)
    managed = _managed_obj(
        project_id=project_id,
        test_suite_id=canonical.test_suite_id,
        test_fingerprint=canonical.test_fingerprint,
    )
    canonical.managed_test_case_id = managed.id
    canonical.source = "linked"
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})

    with patch(
        "app.services.test_suite_service.get_canonical_or_404",
        AsyncMock(return_value=canonical),
    ), patch(
        "app.services.test_suite_service.promote_canonical_test_case",
        AsyncMock(return_value=(canonical, managed)),
    ):
        response = await client.post(
            f"/api/v1/canonical-test-cases/{canonical.id}/promote"
        )

    assert response.status_code == 201, response.text
    assert set(response.json()) == {"canonical", "managed_case"}
    assert response.json()["canonical"]["managed_test_case_id"] == str(managed.id)
    assert response.json()["managed_case"]["status"] == "draft"
    assert response.json()["managed_case"]["test_fingerprint"] == canonical.test_fingerprint


async def test_promote_rejects_cross_project_idor_before_mutation(client, auth_as):
    own_project = uuid.uuid4()
    canonical = _canonical_obj(project_id=uuid.uuid4())
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={own_project})

    with patch(
        "app.services.test_suite_service.get_canonical_or_404",
        AsyncMock(return_value=canonical),
    ), patch(
        "app.services.test_suite_service.promote_canonical_test_case",
        AsyncMock(),
    ) as promote:
        response = await client.post(
            f"/api/v1/canonical-test-cases/{canonical.id}/promote"
        )

    assert response.status_code == 403
    promote.assert_not_awaited()


async def test_unlink_requires_non_blank_reason(client, auth_as):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    response = await client.request(
        "DELETE",
        f"/api/v1/canonical-test-cases/{uuid.uuid4()}/managed-link",
        json={"reason": ""},
    )
    assert response.status_code == 422

    whitespace = await client.request(
        "DELETE",
        f"/api/v1/canonical-test-cases/{uuid.uuid4()}/managed-link",
        json={"reason": "   "},
    )
    assert whitespace.status_code == 422


async def test_unlink_rejects_cross_project_idor_before_mutation(client, auth_as):
    own_project = uuid.uuid4()
    canonical = _canonical_obj(project_id=uuid.uuid4())
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={own_project})

    with patch(
        "app.services.test_suite_service.get_canonical_or_404",
        AsyncMock(return_value=canonical),
    ), patch(
        "app.services.test_suite_service.unlink_canonical_managed_case",
        AsyncMock(return_value=canonical),
    ) as unlink:
        response = await client.request(
            "DELETE",
            f"/api/v1/canonical-test-cases/{canonical.id}/managed-link",
            json={"reason": "Wrong association"},
        )

    assert response.status_code == 403
    unlink.assert_not_awaited()


async def test_confirm_retirement_requires_qa_lead(client, auth_as):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    response = await client.post(
        f"/api/v1/canonical-test-cases/{uuid.uuid4()}/confirm-retirement",
        json={"reason": "Removed intentionally"},
    )
    assert response.status_code == 403


async def test_confirm_retirement_passes_reason_for_accessible_case(
    client, auth_as
):
    project_id = uuid.uuid4()
    canonical = _canonical_obj(
        project_id=project_id,
        status="deleted",
        retirement_confirmed_at=datetime.now(timezone.utc),
        retirement_reason="Removed intentionally",
    )
    auth_as(role=UserRole.QA_LEAD, accessible_projects={project_id})

    with patch(
        "app.services.test_suite_service.get_canonical_or_404",
        AsyncMock(return_value=canonical),
    ), patch(
        "app.services.test_suite_service.confirm_canonical_retirement",
        AsyncMock(return_value=canonical),
    ) as confirm:
        response = await client.post(
            f"/api/v1/canonical-test-cases/{canonical.id}/confirm-retirement",
            json={"reason": "Removed intentionally"},
        )

    assert response.status_code == 200, response.text
    assert confirm.await_args.kwargs["reason"] == "Removed intentionally"


async def test_confirm_retirement_rejects_cross_project_idor_before_mutation(
    client, auth_as
):
    own_project = uuid.uuid4()
    canonical = _canonical_obj(project_id=uuid.uuid4(), status="deleted")
    auth_as(role=UserRole.QA_LEAD, accessible_projects={own_project})

    with patch(
        "app.services.test_suite_service.get_canonical_or_404",
        AsyncMock(return_value=canonical),
    ), patch(
        "app.services.test_suite_service.confirm_canonical_retirement",
        AsyncMock(),
    ) as confirm:
        response = await client.post(
            f"/api/v1/canonical-test-cases/{canonical.id}/confirm-retirement",
            json={"reason": "Removed intentionally"},
        )

    assert response.status_code == 403
    confirm.assert_not_awaited()


# ── S0/S2 authored lifecycle and evidence-gap HTTP contracts ─────────────────


async def test_evidence_gap_route_is_static_validated_and_project_scoped(
    client, auth_as
):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    item = {
        "id": uuid.uuid4(),
        "project_id": project_id,
        "title": "Never run",
        "status": "active",
        "canonical_test_case_id": None,
        "canonical_status": None,
        "deleted_observed_at": None,
        "last_executed_at": None,
    }

    with patch(
        "app.routers.test_management_cases.list_test_case_evidence_gaps",
        AsyncMock(return_value=([item], 1)),
    ) as list_gaps:
        response = await client.get(
            "/api/v1/test-management/cases/evidence-gaps"
            f"?project_id={project_id}&kind=never_executed&page=2&size=11"
        )

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == str(item["id"])
    assert list_gaps.await_args.args[1] == [project_id]
    assert list_gaps.await_args.kwargs["kind"] == "never_executed"
    assert list_gaps.await_args.kwargs["page"] == 2
    assert list_gaps.await_args.kwargs["size"] == 11


async def test_evidence_gap_rejects_foreign_project_and_unknown_kind(
    client, auth_as
):
    own_project = uuid.uuid4()
    foreign_project = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={own_project})

    with patch(
        "app.routers.test_management_cases.list_test_case_evidence_gaps",
        AsyncMock(return_value=([], 0)),
    ) as list_gaps:
        forbidden = await client.get(
            "/api/v1/test-management/cases/evidence-gaps"
            f"?project_id={foreign_project}&kind=automation_vanished"
        )
        invalid = await client.get(
            "/api/v1/test-management/cases/evidence-gaps?kind=surprise"
        )

    assert forbidden.status_code == 403
    assert invalid.status_code == 422
    list_gaps.assert_not_awaited()


async def test_transition_endpoint_preserves_reason_and_returns_allowed_actions(
    client, auth_as
):
    from app.main import app
    from app.routers.test_management_shared import require_case_access

    project_id = uuid.uuid4()
    actor = auth_as(role=UserRole.QA_LEAD, accessible_projects={project_id})
    managed = _managed_obj(
        project_id=project_id,
        author_id=uuid.uuid4(),
        status="active",
    )

    async def _case_access():
        return managed

    app.dependency_overrides[require_case_access] = _case_access
    try:
        with patch(
            "app.routers.test_management_cases.require_lifecycle_v2_enabled",
            AsyncMock(),
        ) as require_flag, patch(
            "app.routers.test_management_cases.transition",
            AsyncMock(return_value=SimpleNamespace(case=managed, review=None)),
        ) as do_transition, patch(
            "app.routers.test_management_cases.lifecycle_actions_for",
            AsyncMock(return_value=["archive"]),
        ):
            response = await client.post(
                f"/api/v1/test-management/cases/{managed.id}/transition",
                json={"action": "deprecate", "reason": "Obsolete workflow"},
            )
    finally:
        app.dependency_overrides.pop(require_case_access, None)

    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(managed.id)
    assert response.json()["allowed_actions"]
    assert do_transition.await_args.args[1] == managed.id
    assert do_transition.await_args.kwargs["reason"] == "Obsolete workflow"
    require_flag.assert_awaited_once()
    assert require_flag.await_args.args[1:] == (managed, actor)


async def test_transition_endpoint_rejects_unknown_action_before_service(
    client, auth_as
):
    from app.main import app
    from app.routers.test_management_shared import require_case_access

    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_LEAD, accessible_projects={project_id})

    async def _case_access():
        return _managed_obj(project_id=project_id)

    app.dependency_overrides[require_case_access] = _case_access
    try:
        with patch(
            "app.routers.test_management_cases.transition", AsyncMock()
        ) as do_transition:
            response = await client.post(
                f"/api/v1/test-management/cases/{uuid.uuid4()}/transition",
                json={"action": "force_publish"},
            )
    finally:
        app.dependency_overrides.pop(require_case_access, None)

    assert response.status_code == 422
    do_transition.assert_not_awaited()


async def test_direct_lifecycle_flag_off_hides_transition_without_mutation(
    client, auth_as
):
    from app.main import app
    from app.routers.test_management_shared import require_case_access

    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_LEAD, accessible_projects={project_id})
    managed = _managed_obj(project_id=project_id, status="active")

    async def _case_access():
        return managed

    app.dependency_overrides[require_case_access] = _case_access
    try:
        with patch(
            "app.routers.test_management_cases.require_lifecycle_v2_enabled",
            AsyncMock(side_effect=HTTPException(status_code=404, detail="disabled")),
        ), patch(
            "app.routers.test_management_cases.transition", AsyncMock()
        ) as do_transition:
            response = await client.post(
                f"/api/v1/test-management/cases/{managed.id}/transition",
                json={"action": "deprecate", "reason": "Obsolete"},
            )
    finally:
        app.dependency_overrides.pop(require_case_access, None)

    assert response.status_code == 404
    do_transition.assert_not_awaited()


async def test_direct_lifecycle_flag_on_exposes_allowed_transitions(
    client, auth_as
):
    from app.main import app
    from app.routers.test_management_shared import require_case_access

    project_id = uuid.uuid4()
    actor = auth_as(role=UserRole.QA_LEAD, accessible_projects={project_id})
    managed = _managed_obj(project_id=project_id, status="active")

    async def _case_access():
        return managed

    app.dependency_overrides[require_case_access] = _case_access
    try:
        with patch(
            "app.routers.test_management_cases.require_lifecycle_v2_enabled",
            AsyncMock(),
        ) as require_flag:
            response = await client.get(
                f"/api/v1/test-management/cases/{managed.id}/allowed-transitions"
            )
    finally:
        app.dependency_overrides.pop(require_case_access, None)

    assert response.status_code == 200, response.text
    actions = {item["action"]: item for item in response.json()}
    assert actions["deprecate"]["allowed"] is True
    require_flag.assert_awaited_once()
    assert require_flag.await_args.args[1:] == (managed, actor)


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("POST", "transition", {"action": "request_review"}),
        ("GET", "allowed-transitions", None),
    ],
)
async def test_direct_lifecycle_routes_fail_closed_on_case_idor(
    client, auth_as, method, suffix, body
):
    from app.main import app
    from app.routers.test_management_shared import require_case_access

    auth_as(role=UserRole.QA_LEAD, accessible_projects={uuid.uuid4()})

    async def _forbidden_case():
        raise HTTPException(status_code=403, detail="Forbidden")

    app.dependency_overrides[require_case_access] = _forbidden_case
    try:
        with patch(
            "app.routers.test_management_cases.require_lifecycle_v2_enabled",
            AsyncMock(),
        ) as require_flag, patch(
            "app.routers.test_management_cases.transition", AsyncMock()
        ) as do_transition:
            response = await client.request(
                method,
                f"/api/v1/test-management/cases/{uuid.uuid4()}/{suffix}",
                json=body,
            )
    finally:
        app.dependency_overrides.pop(require_case_access, None)

    assert response.status_code == 403
    require_flag.assert_not_awaited()
    do_transition.assert_not_awaited()


async def test_invalid_legacy_review_action_is_422_before_service(client, auth_as):
    from app.main import app
    from app.routers.test_management_shared import require_case_access

    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    managed = _managed_obj(project_id=project_id)

    async def _case_access():
        return managed

    app.dependency_overrides[require_case_access] = _case_access
    try:
        with patch(
            "app.routers.test_management_cases.apply_review_action", AsyncMock()
        ) as apply_action:
            response = await client.post(
                f"/api/v1/test-management/cases/{managed.id}/review-action",
                json={"action": "force_approve"},
            )
    finally:
        app.dependency_overrides.pop(require_case_access, None)

    assert response.status_code == 422
    apply_action.assert_not_awaited()


async def test_automation_review_target_returns_actionable_400_at_route(
    client, auth_as
):
    from app.main import app
    from app.routers.test_management_shared import require_case_access_for_review_target

    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})

    async def _automation_access():
        return SimpleNamespace(id=uuid.uuid4(), project_id=project_id)

    app.dependency_overrides[require_case_access_for_review_target] = _automation_access
    try:
        with patch(
            "app.routers.test_management_cases.request_test_case_review",
            AsyncMock(
                side_effect=HTTPException(
                    status_code=400,
                    detail="Automation rows must be promoted before review",
                )
            ),
        ):
            response = await client.post(
                f"/api/v1/test-management/cases/{uuid.uuid4()}/request-review"
            )
    finally:
        app.dependency_overrides.pop(require_case_access_for_review_target, None)

    assert response.status_code == 400
    assert "promoted" in response.json()["detail"].lower()


async def test_case_status_filter_rejects_unknown_enum_before_query(client, auth_as):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    with patch(
        "app.routers.test_management_cases.list_managed_test_cases", AsyncMock()
    ) as list_cases:
        response = await client.get(
            f"/api/v1/test-management/cases?project_id={project_id}&status=surprise"
        )
    assert response.status_code == 422
    list_cases.assert_not_awaited()


async def test_exact_status_filter_excludes_automation_merge(client, auth_as):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    with patch(
        "app.routers.test_management_cases.list_managed_test_cases",
        AsyncMock(return_value=([], 0, 0)),
    ) as list_cases, patch(
        "app.routers.test_management_cases.list_automation_test_cases", AsyncMock()
    ) as list_automation:
        response = await client.get(
            "/api/v1/test-management/cases"
            f"?project_id={project_id}&status=active&include_automation=true"
        )
    assert response.status_code == 200, response.text
    assert list_cases.await_args.kwargs["status"].value == "active"
    list_automation.assert_not_awaited()


async def test_automation_merge_uses_bounded_combined_catalog_contract(client, auth_as):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    with patch(
        "app.routers.test_management_cases.list_combined_test_case_identities",
        AsyncMock(return_value=([], 1001, 6)),
    ) as list_combined:
        response = await client.get(
            "/api/v1/test-management/cases"
            f"?project_id={project_id}&include_automation=true&include_archived=true"
            "&page=6&size=200"
        )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1001
    assert response.json()["pages"] == 6
    assert list_combined.await_args.kwargs == {
        "project_id": project_id,
        "include_archived": True,
        "test_type": None,
        "search": None,
        "suite_name": None,
        "page": 6,
        "size": 200,
    }


@pytest.mark.parametrize(
    "query",
    ["priority=high", "feature_area=checkout", "ai_generated=true"],
)
async def test_managed_only_filters_never_leak_unfilterable_automation_rows(
    client,
    auth_as,
    query,
):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={project_id})
    with patch(
        "app.routers.test_management_cases.list_managed_test_cases",
        AsyncMock(return_value=([], 0, 0)),
    ) as list_managed, patch(
        "app.routers.test_management_cases.list_combined_test_case_identities",
        AsyncMock(),
    ) as list_combined:
        response = await client.get(
            "/api/v1/test-management/cases"
            f"?project_id={project_id}&include_automation=true&{query}"
        )

    assert response.status_code == 200, response.text
    list_managed.assert_awaited_once()
    list_combined.assert_not_awaited()
