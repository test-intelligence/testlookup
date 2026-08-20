"""Regression guard: deleting a project must revoke its credentials.

The defect this guards
----------------------
``DELETE /projects/{id}`` set ``project.is_active = False`` and committed.
Nothing that grants access to the project was touched, so "deleted" revoked
nothing. Measured on a live deployment across its 107 soft-deleted projects:

    api_keys (active)         |   3     <- two with a last_used_at
    project_members           | 264
    qa_lead accounts (active) | 108

The API keys are the sharper half. ``_validate_api_key`` checks the key's own
``is_active``, its expiry and the owner's ``is_active`` — never the project it
is bound to. A CI job holding a key for a deleted project keeps ingesting.

Class: soft-delete leakage (the family already found three times in one file
and four more in search). The guard is written against the behaviour, not
against the two artefact types, so a third credential type bound to a project
that is added later shows up as an uncovered case rather than silently
inheriting the bug.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _result(rows):
    """A db.execute() result whose .scalars().all() yields ``rows``."""
    res = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=rows)
    res.scalars = MagicMock(return_value=scalars)
    res.first = MagicMock(return_value=None)
    return res


def _project(*, lead_id=None, is_active=False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        slug="demo",
        name="Demo Project",
        is_active=is_active,
        default_qa_lead_user_id=lead_id,
    )


@pytest.mark.regression
@pytest.mark.asyncio
async def test_revoke_deactivates_the_qa_lead_account():
    from app.services.project_access_revocation_service import (
        revoke_project_credentials,
    )

    lead = SimpleNamespace(id=uuid.uuid4(), is_active=True)
    project = _project(lead_id=lead.id)
    db = AsyncMock()
    db.get = AsyncMock(return_value=lead)
    # 1: "is another ACTIVE project using this lead?" -> no. 2: API keys -> none.
    db.execute = AsyncMock(side_effect=[_result([]), _result([])])
    db.flush = AsyncMock()

    totals = await revoke_project_credentials(db, project)

    assert lead.is_active is False
    assert totals["qa_lead_accounts"] == 1


@pytest.mark.regression
@pytest.mark.asyncio
async def test_revoke_deactivates_every_api_key_bound_to_the_project():
    from app.services.project_access_revocation_service import (
        revoke_project_credentials,
    )

    keys = [
        SimpleNamespace(id=uuid.uuid4(), is_active=True),
        SimpleNamespace(id=uuid.uuid4(), is_active=True),
    ]
    project = _project(lead_id=None)
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    db.execute = AsyncMock(side_effect=[_result(keys)])
    db.flush = AsyncMock()

    totals = await revoke_project_credentials(db, project)

    assert [k.is_active for k in keys] == [False, False]
    assert totals["api_keys"] == 2


@pytest.mark.regression
@pytest.mark.asyncio
async def test_revoke_spares_a_lead_another_live_project_still_uses():
    """Slugs are unique so sharing should not happen — but deactivating a
    live project's assignee would break its /my-failures inbox, and that is
    not a risk worth taking on an assumption."""
    from app.services.project_access_revocation_service import (
        revoke_project_credentials,
    )

    lead = SimpleNamespace(id=uuid.uuid4(), is_active=True)
    project = _project(lead_id=lead.id)
    db = AsyncMock()
    db.get = AsyncMock(return_value=lead)
    still_used = MagicMock()
    still_used.first = MagicMock(return_value=(uuid.uuid4(),))
    db.execute = AsyncMock(side_effect=[still_used, _result([])])
    db.flush = AsyncMock()

    totals = await revoke_project_credentials(db, project)

    assert lead.is_active is True
    assert totals["qa_lead_accounts"] == 0


@pytest.mark.regression
@pytest.mark.asyncio
async def test_revoke_is_idempotent():
    """A second run reports zeros — it must not flush or log on a no-op."""
    from app.services.project_access_revocation_service import (
        revoke_project_credentials,
    )

    lead = SimpleNamespace(id=uuid.uuid4(), is_active=False)
    project = _project(lead_id=lead.id)
    db = AsyncMock()
    db.get = AsyncMock(return_value=lead)
    db.execute = AsyncMock(side_effect=[_result([])])
    db.flush = AsyncMock()

    totals = await revoke_project_credentials(db, project)

    assert totals == {"qa_lead_accounts": 0, "api_keys": 0}
    db.flush.assert_not_awaited()


@pytest.mark.regression
@pytest.mark.asyncio
async def test_delete_project_calls_the_revocation():
    """The endpoint half. Soft-deleting without revoking is the defect."""
    from app.routers.projects import delete_project

    project = _project(lead_id=uuid.uuid4(), is_active=True)
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=project)
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()

    called: list[object] = []

    async def _spy(session, proj):
        called.append(proj)
        return {"qa_lead_accounts": 1, "api_keys": 0}

    import app.services.project_access_revocation_service as svc
    original = svc.revoke_project_credentials
    svc.revoke_project_credentials = _spy
    try:
        await delete_project(project.id, db=db)
    finally:
        svc.revoke_project_credentials = original

    assert project.is_active is False
    assert called == [project], "delete_project soft-deleted without revoking"
    # Revocation must land in the SAME transaction as the soft delete, so a
    # failed commit cannot leave the project deleted with its keys live.
    db.commit.assert_awaited_once()


@pytest.mark.regression
@pytest.mark.asyncio
async def test_reconcile_sweeps_projects_deleted_before_the_fix():
    """The code fix only covers future deletes; existing rows need a sweep.

    The statement is inspected, not just the return value. A mocked
    ``db.execute`` hands back the same list whatever it is asked for, so an
    assertion on the results alone survives the sweep being pointed at
    ACTIVE projects — which is both wrong and destructive. Verified: that
    exact mutation did not kill this guard until the SQL assertion below
    was added.
    """
    import app.services.project_access_revocation_service as svc

    projects = [_project(), _project(), _project()]
    statements: list[object] = []

    async def _execute(stmt):
        statements.append(stmt)
        return _result(projects)

    db = AsyncMock()
    db.execute = _execute

    seen: list[object] = []

    async def _spy(session, proj):
        seen.append(proj)
        return {"qa_lead_accounts": 1, "api_keys": 2}

    original = svc.revoke_project_credentials
    svc.revoke_project_credentials = _spy
    try:
        totals = await svc.reconcile_deleted_project_credentials(db)
    finally:
        svc.revoke_project_credentials = original

    assert seen == projects
    assert totals == {"projects": 3, "qa_lead_accounts": 3, "api_keys": 6}

    sql = " ".join(str(statements[0]).split())
    assert "projects.is_active IS false" in sql, (
        "the sweep must select DELETED projects; selecting active ones would "
        f"revoke credentials for live projects. Got: {sql}"
    )
