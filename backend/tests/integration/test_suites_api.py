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

from fastapi import HTTPException, status  # noqa: E402

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
        "deleted_at_run_id": None,
        "managed_test_case_id": None,
        "review_tag": None,
        "tags": None,
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
