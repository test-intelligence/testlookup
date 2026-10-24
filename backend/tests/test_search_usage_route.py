"""W12 SRCH-05: ``GET /search/usage`` -- QA lead and up, the project authorised first.

Through the ASGI app with the REAL ``require_role(UserRole.QA_LEAD)``
dependency and the real ``resolve_project_scope`` (memberships patched), so
the refusals are the ones a browser gets. The summary itself is G-A's
``search_usage_service.usage_summary`` (pinned in its own tests); here it is
patched and only its arguments are read.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.models.postgres import User, UserRole

PROJECT, OTHER = uuid.uuid4(), uuid.uuid4()
SUMMARY = {
    "project_id": None, "days": 1, "since": "2026-10-24T00:00:00+00:00", "measured": False, "searches": 0,
    "zero_result_searches": 0, "zero_result_rate": None, "p50_latency_ms": None, "p95_latency_ms": None,
    "fallback_searches": 0, "retention_days": 30,
}


def _user(role: UserRole) -> User:
    return User(id=uuid.uuid4(), username=f"u-{role.value}", email=f"{role.value}@example.test", role=role, is_active=True)


@pytest.fixture
def usage(monkeypatch):
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import search as router

    async def accessible(db, user):
        return None if user.role == UserRole.ADMIN else {PROJECT}

    monkeypatch.setattr("app.core.deps.get_accessible_project_ids", accessible)
    summary = AsyncMock(side_effect=lambda db, **kw: {**SUMMARY, "project_id": kw["project_id"], "days": kw["days"]})
    monkeypatch.setattr(router, "usage_summary", summary)
    app = FastAPI()
    app.include_router(router.router)
    caller: dict = {}
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_current_active_user] = lambda: caller["user"]

    async def get(role: UserRole, **params):
        caller["user"] = _user(role)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            return await client.get("/api/v1/search/usage", params=params)

    return get, summary


@pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.TESTER, UserRole.QA_ENGINEER])
async def test_below_qa_lead_is_refused_before_any_read(usage, role):
    get, summary = usage
    response = await get(role, project_id=str(PROJECT))
    assert response.status_code == 403
    assert response.json()["detail"] == "Requires at least QA_LEAD role"
    summary.assert_not_awaited()


async def test_a_qa_lead_naming_another_project_is_refused(usage):
    get, summary = usage
    response = await get(UserRole.QA_LEAD, project_id=str(OTHER))
    assert response.status_code == 403
    assert response.json()["detail"] == "You do not have access to this project"
    summary.assert_not_awaited()


async def test_a_malformed_project_is_400(usage):
    get, _ = usage
    response = await get(UserRole.QA_LEAD, project_id="nope")
    assert response.status_code == 400 and response.json()["detail"] == "Invalid project ID"


@pytest.mark.parametrize("days", ["2", "0", "365"])
async def test_days_outside_1_7_30_is_422_with_the_sentence(usage, days):
    get, summary = usage
    response = await get(UserRole.QA_LEAD, project_id=str(PROJECT), days=days)
    assert response.status_code == 422
    assert response.json()["detail"] == "days must be 1, 7 or 30."
    summary.assert_not_awaited()


async def test_a_member_lead_reads_their_project(usage):
    get, summary = usage
    response = await get(UserRole.QA_LEAD, project_id=str(PROJECT), days="7")
    assert response.status_code == 200, response.text
    assert response.json()["measured"] is False and response.json()["zero_result_rate"] is None
    kwargs = summary.await_args.kwargs
    assert kwargs["project_id"] == PROJECT and kwargs["allowed_project_ids"] is None and kwargs["days"] == 7


async def test_without_a_project_a_lead_sees_their_projects_and_an_admin_the_deployment(usage):
    get, summary = usage
    assert (await get(UserRole.QA_LEAD)).status_code == 200
    assert summary.await_args.kwargs["project_id"] is None
    assert summary.await_args.kwargs["allowed_project_ids"] == {PROJECT}
    assert summary.await_args.kwargs["days"] == 1
    assert (await get(UserRole.ADMIN)).status_code == 200
    assert summary.await_args.kwargs["allowed_project_ids"] is None
