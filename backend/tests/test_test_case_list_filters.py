"""W13 TCL-01: ``GET /api/v1/test-management/cases`` takes several values, an owner and an automation state.

The REAL router over HTTP (its own small app), the list services patched to
record what they are asked, and a session that FAILS on any statement — so
``owner=me`` provably costs no query, and a refusal provably comes before one.

* ``status`` / ``priority`` / ``test_type`` repeat; one value keeps today's
  call shape (``status`` the enum); more than 20 values -> 422;
* ``owner``: ``me`` is the reader; a user id must be assignable in the scoped
  project (``require_assignable``), with the filter's own 422 sentence; a user
  id with no project and a non-id are 422;
* ``automation``: ``automated`` | ``not_automated``, else 422;
* any of the new filters takes the authored-only path and the answer says
  ``automation_rows_excluded: true`` when automation rows were asked for;
* the project scope is judged FIRST (a non-member is 403 before any filter).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException

pytest.importorskip("httpx")

from app.core.test_library_vocab import (  # noqa: E402
    AUTOMATION_INVALID_SENTENCE,
    OWNER_INVALID_SENTENCE,
    OWNER_NEEDS_PROJECT_SENTENCE,
    OWNER_NOT_MEMBER_SENTENCE,
    PRIORITY_INVALID_SENTENCE,
    TOO_MANY_VALUES_SENTENCE,
    TYPE_INVALID_SENTENCE,
)

URL = "/api/v1/test-management/cases"


class _NoStatementSession:
    """Every list read is patched: a statement here is a test failure."""

    def __init__(self):
        self.statements: list = []

    async def execute(self, stmt, *_a, **_k):
        self.statements.append(stmt)
        raise AssertionError(f"unexpected statement: {stmt}")


@pytest_asyncio.fixture
async def harness(monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core import deps
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import test_management_cases as cases
    from app.services import test_management_service as service

    project_id = uuid.uuid4()
    state = SimpleNamespace(
        db=_NoStatementSession(),
        user=SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER", is_active=True, username="priya"),
        accessible={project_id},
        project_id=project_id,
        managed=AsyncMock(return_value=([], 0, 0)),
        combined=AsyncMock(return_value=([], 0, 0)),
        assignable=AsyncMock(return_value=None),
    )

    async def _accessible(_db, _user):
        return state.accessible

    monkeypatch.setattr(deps, "get_accessible_project_ids", _accessible)
    monkeypatch.setattr(cases, "list_managed_test_cases", state.managed)
    monkeypatch.setattr(cases, "list_combined_test_case_identities", state.combined)
    monkeypatch.setattr(cases, "list_automation_test_cases", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "require_assignable", state.assignable)

    app = FastAPI()
    app.include_router(cases.router, prefix="/api/v1/test-management")
    app.dependency_overrides[get_db] = lambda: state.db
    app.dependency_overrides[get_current_active_user] = lambda: state.user
    state.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        yield state
    finally:
        await state.client.aclose()


async def _get(harness, query: str, *, project: bool = True):
    prefix = f"project_id={harness.project_id}&" if project else ""
    return await harness.client.get(f"{URL}?{prefix}{query}")


@pytest.mark.asyncio
async def test_repeated_statuses_take_the_authored_path_and_one_keeps_todays_shape(harness):
    resp = await _get(harness, "status=draft&status=review_requested&status=draft")
    assert resp.status_code == 200, resp.text
    kwargs = harness.managed.await_args.kwargs
    assert kwargs["filters"].statuses == ("draft", "review_requested")
    assert kwargs["status"] is None
    assert kwargs["search_requirements"] is True

    harness.managed.reset_mock()
    resp = await _get(harness, "status=draft&priority=high&priority=critical")
    assert resp.status_code == 200, resp.text
    kwargs = harness.managed.await_args.kwargs
    assert kwargs["status"].value == "draft"  # today's single-value call shape
    assert kwargs["filters"].statuses == ("draft",)
    assert kwargs["priority"] is None and kwargs["filters"].priorities == ("high", "critical")


@pytest.mark.asyncio
async def test_priorities_take_the_p0_to_p3_aliases_and_refuse_anything_else(harness):
    resp = await _get(harness, "priority=P0")
    assert resp.status_code == 200, resp.text
    kwargs = harness.managed.await_args.kwargs
    assert kwargs["priority"] == "critical"  # one value: today's call shape, stored spelling
    assert kwargs["filters"].priorities == ("critical",)

    resp = await _get(harness, "priority=p0&priority=Critical&priority=P1&priority=medium")
    assert resp.status_code == 200, resp.text
    kwargs = harness.managed.await_args.kwargs
    assert kwargs["filters"].priorities == ("critical", "high", "medium")
    assert kwargs["priority"] is None

    harness.managed.reset_mock()
    for bad in ("urgent", "P4", "p0x"):
        resp = await _get(harness, f"priority={bad}")
        assert (resp.status_code, resp.json()["detail"]) == (422, PRIORITY_INVALID_SENTENCE), bad
    harness.managed.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unknown_status_and_an_overlong_type_are_refused(harness):
    resp = await _get(harness, "status=draft&status=bogus")
    assert resp.status_code == 422  # the route's own enum (FastAPI's answer, as before W13)
    resp = await _get(harness, "test_type=" + "x" * 51)
    assert (resp.status_code, resp.json()["detail"]) == (422, TYPE_INVALID_SENTENCE)
    # Types stay free text: a type outside the chip's nine still filters.
    resp = await _get(harness, "test_type=accessibility&test_type=e2e")
    assert resp.status_code == 200, resp.text
    assert harness.managed.await_args.kwargs["filters"].test_types == ("accessibility", "e2e")


@pytest.mark.asyncio
async def test_more_than_twenty_values_of_one_filter_is_refused(harness):
    many = "&".join(f"test_type=type-{i}" for i in range(21))
    resp = await _get(harness, many)
    assert resp.status_code == 422
    assert resp.json()["detail"] == TOO_MANY_VALUES_SENTENCE
    harness.managed.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_me_is_the_reader_and_costs_no_statement(harness):
    resp = await _get(harness, "owner=me")
    assert resp.status_code == 200, resp.text
    assert harness.managed.await_args.kwargs["filters"].owner_id == harness.user.id
    assert harness.db.statements == []
    harness.assignable.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_owner_id_must_be_assignable_in_the_scoped_project(harness):
    priya = uuid.uuid4()
    resp = await _get(harness, f"owner={priya}")
    assert resp.status_code == 200, resp.text
    harness.assignable.assert_awaited_once_with(harness.db, harness.project_id, priya)
    assert harness.managed.await_args.kwargs["filters"].owner_id == priya

    harness.assignable.side_effect = HTTPException(
        status_code=422, detail="The assignee must be an active member of this project.",
    )
    resp = await _get(harness, f"owner={uuid.uuid4()}")
    assert (resp.status_code, resp.json()["detail"]) == (422, OWNER_NOT_MEMBER_SENTENCE)

    resp = await _get(harness, "owner=bob")
    assert (resp.status_code, resp.json()["detail"]) == (422, OWNER_INVALID_SENTENCE)


@pytest.mark.asyncio
async def test_an_owner_id_without_a_project_is_refused_for_an_admin(harness):
    harness.user.role = "ADMIN"
    harness.accessible = None
    resp = await _get(harness, f"owner={uuid.uuid4()}", project=False)
    assert (resp.status_code, resp.json()["detail"]) == (422, OWNER_NEEDS_PROJECT_SENTENCE)
    # ``me`` works in every scope.
    resp = await _get(harness, "owner=me", project=False)
    assert resp.status_code == 200, resp.text
    assert harness.managed.await_args.kwargs["filters"].owner_id == harness.user.id


@pytest.mark.asyncio
async def test_automation_and_the_authored_only_answer(harness):
    resp = await _get(harness, "automation=yes")
    assert (resp.status_code, resp.json()["detail"]) == (422, AUTOMATION_INVALID_SENTENCE)

    resp = await _get(harness, "include_automation=true&owner=me")
    assert resp.status_code == 200, resp.text
    assert resp.json()["automation_rows_excluded"] is True
    harness.combined.assert_not_awaited()

    resp = await _get(harness, "include_automation=true&automation=not_automated")
    assert resp.json()["automation_rows_excluded"] is True
    assert harness.managed.await_args.kwargs["filters"].automation == "not_automated"

    resp = await _get(harness, "include_automation=true&requirement=%5Breq-pay-07%5D")
    assert resp.json()["automation_rows_excluded"] is True
    assert harness.managed.await_args.kwargs["filters"].requirement_key == "REQ-PAY-07"

    resp = await _get(harness, "include_automation=true")
    assert resp.status_code == 200, resp.text
    assert resp.json()["automation_rows_excluded"] is False
    harness.combined.assert_awaited_once()

    harness.combined.reset_mock()
    resp = await _get(harness, "include_automation=true&test_type=e2e")
    assert resp.status_code == 200, resp.text
    assert harness.combined.await_args.kwargs["test_type"] == "e2e"
    assert "search_requirements" not in harness.combined.await_args.kwargs

    resp = await _get(harness, "include_automation=true&search=pay-07")
    assert harness.combined.await_args.kwargs["search_requirements"] is True

    resp = await _get(harness, "automation=automated")
    assert resp.json()["automation_rows_excluded"] is False  # automation rows were not asked for


@pytest.mark.asyncio
async def test_the_project_scope_is_judged_before_any_filter(harness):
    harness.accessible = {uuid.uuid4()}  # the reader is not a member of the named project
    resp = await _get(harness, "owner=bob&automation=yes")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "You do not have access to this project"
    harness.managed.assert_not_awaited()
    harness.assignable.assert_not_awaited()

    resp = await _get(harness, "requirement=" + "R" * 201)
    assert resp.status_code == 422  # the QUERY bound (FastAPI's own answer)


@pytest.mark.asyncio
async def test_the_evidence_gap_route_accepts_the_two_new_kinds_and_refuses_others(harness, monkeypatch):
    """W13 TCL-03 (the route half): ``kind`` follows ``EVIDENCE_GAP_KIND_PATTERN``."""
    from app.core.test_library_vocab import EVIDENCE_GAP_KINDS
    from app.routers import test_management_cases as cases

    gaps = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(cases, "list_test_case_evidence_gaps", gaps)
    for kind in EVIDENCE_GAP_KINDS:
        resp = await harness.client.get(f"{URL}/evidence-gaps?project_id={harness.project_id}&kind={kind}")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"items": [], "total": 0}
        assert gaps.await_args.kwargs["kind"] == kind
    assert {"flaky_active", "no_owner"} <= set(EVIDENCE_GAP_KINDS)

    gaps.reset_mock()
    for bad in ("bogus", "flaky_active_extra", "no_owner%20"):
        resp = await harness.client.get(f"{URL}/evidence-gaps?project_id={harness.project_id}&kind={bad}")
        assert resp.status_code == 422, bad
    gaps.assert_not_awaited()
