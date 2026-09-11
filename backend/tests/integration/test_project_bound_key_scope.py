"""Re-audit N20: a project-bound API key stays inside its project everywhere.

Only an ADMIN can bind a key to a project, so a bound key is usually an ADMIN's
CI credential. ``require_role(UserRole.ADMIN)`` now refuses it by default
(``tests/test_project_scoped_key_auth.py``); this file covers the places that
decided on the ADMIN role by some other road and let the same key reach every
tenant:

* the debug data generator, which filed runs into any project it was named;
* the release-gate-policy writes, which never looked at the policy's project;
* ``POST /api/v1/keys``, which minted the key an UNBOUND key, or one for anyone;
* the reindex, investigation, fix-attempt, chat-session, share-link, key-owner,
  reassignment, triage and activity-export checks, each of which returned early
  for ADMIN before looking at the binding.

Every "refused" case here was a success before N20. The matching "own project"
cases prove the refusal is the binding and not a blanket ban.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

pytest.importorskip("httpx")
pytest.importorskip("jose")

from app.core.deps import (  # noqa: E402
    CREDENTIAL_KIND_API_KEY,
    PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL,
    _bind_api_key_project,
    _bind_credential_kind,
    require_api_key_owner,
    require_link_access,
    require_session_access,
)
from app.models.postgres import UserRole  # noqa: E402

pytestmark = pytest.mark.asyncio

BOUND = uuid.uuid4()
OTHER = uuid.uuid4()


def _bound_admin(project_id=BOUND, role=UserRole.ADMIN):
    """What ``_validate_api_key`` hands the dependencies for a project-scoped key."""
    user = SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True, username="ci")
    _bind_api_key_project(user, project_id)
    return _bind_credential_kind(user, CREDENTIAL_KIND_API_KEY)


def _unbound_admin():
    user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.ADMIN, is_active=True, username="admin")
    return _bind_api_key_project(user, None)


def _db(*scalars):
    """A session whose successive ``execute`` calls return these scalars."""
    results = []
    for value in scalars:
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=value)
        results.append(result)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=results)
    return db


async def _refused(awaitable) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        await awaitable
    assert exc.value.status_code == 403, exc.value.detail
    return exc.value


# ── POST /api/v1/debug/generate-test-run ─────────────────────────────────────

DEBUG_URL = "/api/v1/debug/generate-test-run"


async def test_a_bound_key_cannot_generate_runs_even_into_its_own_project(client, auth_as):
    auth_as(role=UserRole.ADMIN, bound_project_id=BOUND)

    resp = await client.post(DEBUG_URL, params={"project_id": str(BOUND)})

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL


async def test_a_missing_project_is_404_and_nothing_is_written(client, auth_as):
    auth_as(role=UserRole.ADMIN)
    storage = MagicMock()
    storage.put_object = AsyncMock()

    with patch("app.routers.debug.get_storage_provider", return_value=storage), patch(
        "app.routers.debug.ingest_test_run"
    ) as task:
        resp = await client.post(DEBUG_URL, params={"project_id": str(uuid.uuid4())})

    assert resp.status_code == 404, resp.text
    storage.put_object.assert_not_awaited()
    task.delay.assert_not_called()


async def test_an_instance_admin_still_generates_into_an_existing_project(client, auth_as, fake_db):
    from tests.integration.conftest import fake_execute_result

    auth_as(role=UserRole.ADMIN)
    fake_db.set_execute_results([fake_execute_result(scalar=BOUND)])
    storage = MagicMock()
    storage.put_object = AsyncMock()

    with patch("app.routers.debug.get_storage_provider", return_value=storage), patch(
        "app.routers.debug.ingest_test_run"
    ) as task:
        resp = await client.post(
            DEBUG_URL, params={"project_id": str(BOUND), "num_tests": 2, "report_type": "allure"}
        )

    assert resp.status_code == 202, resp.text
    task.delay.assert_called_once()


# ── release-gate policies ────────────────────────────────────────────────────


async def test_a_bound_key_cannot_create_a_policy_for_another_project():
    from app.models.schemas import ReleaseGatePolicyCreate
    from app.routers.release_gate_policies import create_policy

    payload = ReleaseGatePolicyCreate(project_id=OTHER, name="other project")
    await _refused(create_policy(payload=payload, current_user=_bound_admin(), db=_db()))


async def test_a_bound_key_cannot_create_the_system_default():
    from app.models.schemas import ReleaseGatePolicyCreate
    from app.routers.release_gate_policies import SYSTEM_DEFAULT_REFUSED_DETAIL, create_policy

    payload = ReleaseGatePolicyCreate(project_id=None, name="system default")
    exc = await _refused(create_policy(payload=payload, current_user=_bound_admin(), db=_db()))
    assert exc.detail == SYSTEM_DEFAULT_REFUSED_DETAIL


@pytest.mark.parametrize("owner_project", [OTHER, None], ids=["other-project", "system-default"])
@pytest.mark.parametrize("action", ["update", "publish", "deactivate"])
async def test_a_bound_key_cannot_change_a_policy_outside_its_project(action, owner_project):
    from app.models.schemas import ReleaseGatePolicyUpdate
    from app.routers import release_gate_policies as rgp

    policy = SimpleNamespace(project_id=owner_project, is_draft=True, is_active=False)
    db = _db(policy)
    user = _bound_admin()
    if action == "update":
        call = rgp.update_policy(
            policy_id=uuid.uuid4(), payload=ReleaseGatePolicyUpdate(name="x2"),
            current_user=user, db=db,
        )
    elif action == "publish":
        call = rgp.publish_policy(policy_id=uuid.uuid4(), current_user=user, db=db)
    else:
        call = rgp.deactivate_policy(policy_id=uuid.uuid4(), current_user=user, db=db)

    await _refused(call)
    db.commit.assert_not_awaited()
    assert policy.is_active is False and policy.is_draft is True


async def test_a_bound_key_may_deactivate_its_own_projects_policy():
    from app.routers.release_gate_policies import deactivate_policy

    policy = SimpleNamespace(
        project_id=BOUND, is_draft=False, is_active=True, name="own", version=1
    )
    db = _db(policy)

    result = await deactivate_policy(policy_id=uuid.uuid4(), current_user=_bound_admin(), db=db)

    assert result is policy and policy.is_active is False
    db.commit.assert_awaited_once()


async def test_a_bound_key_cannot_simulate_against_another_projects_run():
    from app.models.schemas import PolicyDocument, PolicySimulateRequest
    from app.routers.release_gate_policies import simulate_policy

    payload = PolicySimulateRequest(run_id=uuid.uuid4(), policy_document=PolicyDocument())
    await _refused(simulate_policy(payload=payload, current_user=_bound_admin(), db=_db(OTHER)))


async def test_a_bound_key_passes_the_binding_for_its_own_run():
    """Past the binding, the ordinary 404 for a run with no decision."""
    from app.models.schemas import PolicyDocument, PolicySimulateRequest
    from app.routers.release_gate_policies import simulate_policy

    payload = PolicySimulateRequest(run_id=uuid.uuid4(), policy_document=PolicyDocument())
    with pytest.raises(HTTPException) as exc:
        await simulate_policy(payload=payload, current_user=_bound_admin(), db=_db(BOUND, None))
    assert exc.value.status_code == 404
    assert "release decision" in exc.value.detail


# ── /api/v1/keys ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fields",
    [{}, {"project_id": OTHER}, {"project_id": BOUND, "target_user_id": uuid.uuid4()}],
    ids=["unbound-key", "other-project", "someone-elses-key"],
)
async def test_a_bound_key_mints_nothing_wider_than_itself(fields):
    from app.models.schemas import ApiKeyCreate
    from app.routers.api_keys import create_api_key

    db = _db()
    db.add = MagicMock()
    await _refused(
        create_api_key(payload=ApiKeyCreate(name="rotate", **fields), db=db, current_user=_bound_admin())
    )
    db.add.assert_not_called()


async def test_a_bound_key_cannot_list_another_projects_keys():
    from app.routers.api_keys import list_api_keys

    await _refused(list_api_keys(project_id=OTHER, db=_db(), current_user=_bound_admin()))


async def test_a_bound_key_lists_only_its_own_projects_keys():
    from app.routers.api_keys import list_api_keys

    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    await list_api_keys(project_id=None, db=db, current_user=_bound_admin())

    # The WHERE clause, not the whole statement: the SELECT list names
    # api_keys.project_id too, so checking str(stmt) could never fail.
    where = str(db.execute.await_args.args[0].whereclause)
    assert "api_keys.project_id" in where, where


@pytest.mark.parametrize("key_project", [OTHER, None], ids=["other-project", "unbound-key"])
async def test_a_bound_key_cannot_revoke_a_key_outside_its_project(key_project):
    user = _bound_admin()
    guard = require_api_key_owner()
    request = SimpleNamespace(path_params={"key_id": str(uuid.uuid4())})

    await _refused(guard(request, _db(user.id, key_project), user))


async def test_a_bound_key_may_revoke_its_own_projects_key():
    user = _bound_admin()
    guard = require_api_key_owner()
    request = SimpleNamespace(path_params={"key_id": str(uuid.uuid4())})

    assert await guard(request, _db(user.id, BOUND), user) is user


# ── POST /api/v1/search/reindex ──────────────────────────────────────────────


async def _reindex(user, project_id):
    from app.routers import search as search_router

    queued: list = []
    fake_tasks = MagicMock()
    fake_tasks.reindex_search.apply_async = lambda kwargs=None, **_: (
        queued.append(kwargs) or SimpleNamespace(id="t-1")
    )
    with patch.dict("sys.modules", {"app.worker.tasks": fake_tasks}):
        await search_router.trigger_reindex(
            project_id=project_id, full=True, db=AsyncMock(), current_user=user
        )
    return queued


async def test_a_bound_key_cannot_reindex_every_tenant():
    with pytest.raises(HTTPException) as exc:
        await _reindex(_bound_admin(), None)
    assert exc.value.status_code == 403


async def test_a_bound_key_may_reindex_its_own_project():
    queued = await _reindex(_bound_admin(), str(BOUND))
    assert queued and queued[0]["project_id"] == str(BOUND)


# ── resource guards that returned early for ADMIN ────────────────────────────


@pytest.mark.parametrize(
    "module, factory, param",
    [
        ("app.routers.agent_investigations", "require_investigation_access", "investigation_id"),
        ("app.routers.fixer", "require_attempt_access", "attempt_id"),
    ],
)
async def test_a_bound_admin_key_reaches_only_its_own_projects_resources(module, factory, param):
    import importlib

    guard = getattr(importlib.import_module(module), factory)()
    request = SimpleNamespace(path_params={param: str(uuid.uuid4())})
    user = _bound_admin()

    await _refused(guard(request=request, db=_db(OTHER), current_user=user))
    assert await guard(request=request, db=_db(BOUND), current_user=user) is user


@pytest.mark.parametrize("session_project", [OTHER, None], ids=["other-project", "no-project"])
async def test_a_bound_key_cannot_open_chat_outside_its_project(session_project):
    user = _bound_admin()
    session = SimpleNamespace(id=uuid.uuid4(), user_id=user.id, project_id=session_project)
    request = SimpleNamespace(path_params={"session_id": str(session.id)})

    await _refused(require_session_access()(request, _db(session), user))


async def test_a_bound_key_opens_its_own_projects_chat():
    user = _bound_admin()
    session = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), project_id=BOUND)
    request = SimpleNamespace(path_params={"session_id": str(session.id)})

    assert await require_session_access()(request, _db(session), user) is session


async def test_a_bound_key_cannot_revoke_another_projects_share_link():
    link = SimpleNamespace(id=uuid.uuid4(), project_id=OTHER, created_by_id=uuid.uuid4())
    request = SimpleNamespace(path_params={"link_id": str(link.id)})

    await _refused(require_link_access()(request, _db(link), _bound_admin()))


async def test_an_unbound_admin_still_bypasses_every_resource_guard():
    user = _unbound_admin()
    session = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), project_id=OTHER)
    link = SimpleNamespace(id=uuid.uuid4(), project_id=OTHER, created_by_id=uuid.uuid4())

    assert await require_session_access()(
        SimpleNamespace(path_params={"session_id": str(session.id)}), _db(session), user
    ) is session
    assert await require_link_access()(
        SimpleNamespace(path_params={"link_id": str(link.id)}), _db(link), user
    ) is link


# ── services and helpers with their own ADMIN return ─────────────────────────


async def test_reassignment_authority_stops_at_the_binding():
    from app.services.failed_test_reassignment_service import (
        ReassignmentError,
        _actor_has_authority,
    )

    with pytest.raises(ReassignmentError) as exc:
        await _actor_has_authority(_db(), _bound_admin(), OTHER)
    assert exc.value.status_code == 403
    assert await _actor_has_authority(_db(), _bound_admin(), BOUND) is True


async def test_triage_authority_stops_at_the_binding_even_for_the_assignee():
    from app.services.failed_test_triage_service import TriageError, _actor_can_triage

    user = _bound_admin()
    tc = SimpleNamespace(assigned_to_user_id=user.id)
    with pytest.raises(TriageError) as exc:
        await _actor_can_triage(_db(), user, tc, OTHER)
    assert exc.value.status_code == 403
    assert await _actor_can_triage(_db(), user, tc, BOUND) is True


async def test_activity_export_stops_at_the_binding():
    from app.routers.activity import _assert_can_export

    await _refused(_assert_can_export(_db(), _bound_admin(), OTHER))
    assert await _assert_can_export(_db(), _bound_admin(), BOUND) is None
