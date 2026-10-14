"""GATE-06: revoking a report share link is an IDOR-guarded write.

The release gate now lists a run's share links and offers **Revoke** on each
(``DELETE /api/v1/reports/share-links/{link_id}``). The link id is the only
input, so the route must refuse a caller who is neither the link's creator, a
member of the link's project, nor an admin — and must refuse a project-bound
API key reaching another project's link — before it flips ``is_revoked`` or
writes an audit row. The guard is unit-tested in ``test_authorization_guards``;
this drives the real route through FastAPI so a route that lost the guard (or
wrote before checking) fails here.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.regression

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER = uuid.UUID("22222222-2222-4222-8222-222222222222")
CREATOR = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _user(role: str = "QA_LEAD", *, user_id: uuid.UUID | None = None, bound_to: uuid.UUID | None = None):
    user = SimpleNamespace(id=user_id or uuid.uuid4(), role=role, is_active=True, username="caller")
    if bound_to is not None:
        # How deps marks a project-bound API key (``_API_KEY_BOUND_PROJECT_ATTR``).
        setattr(user, "_testlookup_api_key_project_id", bound_to)
    return user


def _scalar(value):
    return MagicMock(scalar_one_or_none=MagicMock(return_value=value))


@pytest.fixture
def world():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import reports

    app = FastAPI()
    app.include_router(reports.router)
    link = SimpleNamespace(
        id=uuid.uuid4(), project_id=PROJECT, run_id=uuid.uuid4(), created_by_id=CREATOR, is_revoked=False,
    )
    state = SimpleNamespace(user=_user(), member=False, link=link)
    db = MagicMock()
    db.commit = AsyncMock()

    async def _execute(statement, *args, **kwargs):
        text = str(statement)
        if "report_share_links" in text:
            return _scalar(state.link)
        if "project_members" in text:
            return _scalar(uuid.uuid4() if state.member else None)
        raise AssertionError(f"unexpected query: {text}")

    db.execute = AsyncMock(side_effect=_execute)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state.user
    state.db = db
    state.http = TestClient(app, raise_server_exceptions=False)
    return state


def _revoke(world, link_id=None):
    return world.http.delete(f"/api/v1/reports/share-links/{link_id or world.link.id}")


def _audit_actions(world):
    return [call.args[0].action for call in world.db.add.call_args_list]


@pytest.mark.parametrize("role", ["VIEWER", "QA_ENGINEER", "QA_LEAD"])
def test_a_non_member_who_did_not_create_the_link_cannot_revoke_it(world, role):
    world.user = _user(role)
    world.member = False

    resp = _revoke(world)

    assert resp.status_code == 403, resp.text
    assert world.link.is_revoked is False
    assert _audit_actions(world) == []
    world.db.commit.assert_not_awaited()


def test_a_key_bound_to_another_project_cannot_revoke_even_as_admin(world):
    world.user = _user("ADMIN", bound_to=OTHER)

    resp = _revoke(world)

    assert resp.status_code == 403, resp.text
    assert world.link.is_revoked is False
    world.db.commit.assert_not_awaited()


def test_a_project_member_revokes_it_and_the_revocation_is_audited(world):
    world.user = _user("QA_ENGINEER")
    world.member = True

    resp = _revoke(world)

    assert resp.status_code == 204, resp.text
    assert world.link.is_revoked is True
    assert _audit_actions(world) == ["report_share_revoked"]
    world.db.commit.assert_awaited_once()


def test_the_creator_revokes_it_without_a_membership(world):
    world.user = _user("QA_ENGINEER", user_id=CREATOR)
    world.member = False

    resp = _revoke(world)

    assert resp.status_code == 204, resp.text
    assert world.link.is_revoked is True


def test_an_unknown_link_is_404_and_nothing_is_written(world):
    world.link = None

    resp = _revoke(world, link_id=uuid.uuid4())

    assert resp.status_code == 404, resp.text
    world.db.commit.assert_not_awaited()
