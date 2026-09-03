"""Unit tests for the projects router's ``default_qa_lead_user_id`` role
validation (2026-05-14 feature, migration 0079).

The router function-level tests bypass FastAPI's dependency wiring (no
real DB, no real auth) so we can pin two contracts cheaply:

  1. ``PUT /projects/{id}`` calls ``assert_user_is_qa_lead_on_project``
     BEFORE applying any field updates when the payload includes
     ``default_qa_lead_user_id``. A 400 from the role check must abort
     the update — no field is written, no commit happens.

  2. ``PUT /projects/{id}`` SKIPS the role check when the payload doesn't
     include the field — touching other fields shouldn't trigger the
     validator.

  3. ``POST /projects`` calls the validator after the creator's
     ProjectMember row is staged (so the creator themselves can be set
     as default in one round trip).

The integration tests in tests/integration/ exercise the full FastAPI
stack; here we test the function bodies in isolation.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _savepoint_capable(db):
    """Give an AsyncMock session a working ``begin_nested()``.

    ``AsyncMock.begin_nested()`` returns a coroutine, not an async context
    manager, so ``async with db.begin_nested():`` raises TypeError. Project
    creation now provisions the project's active release (migration 0150), and
    that insert is SAVEPOINT-wrapped for race-safety — so any fake session
    reaching create_project needs this.
    """
    savepoint = AsyncMock()
    savepoint.__aenter__ = AsyncMock(return_value=savepoint)
    savepoint.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=savepoint)
    return db

def _scalar(value):
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=value)
    return res


# ── PUT /projects/{id} ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_project_runs_role_check_when_field_present():
    """Setting default_qa_lead_user_id must invoke the role check."""
    from app.routers.projects import update_project
    from app.models.schemas import ProjectUpdate

    project_id = uuid.uuid4()
    project = SimpleNamespace(
        id=project_id, name="P", description=None, default_qa_lead_user_id=None,
        manager_user_id=None,
    )
    db = _savepoint_capable(AsyncMock())
    db.execute = AsyncMock(return_value=_scalar(project))
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    qa_lead_id = uuid.uuid4()
    payload = ProjectUpdate(default_qa_lead_user_id=qa_lead_id)

    # Patch the validator so we don't need to mock its DB queries here —
    # the contract under test is "the router CALLS the validator," not
    # "the validator works correctly" (that's covered by
    # test_qa_lead_owner_assignment.py).
    with patch(
        "app.services.suite_review_service.assert_user_is_qa_lead_on_project",
        new=AsyncMock(return_value=None),
    ) as mock_assert:
        result = await update_project(project_id, payload, db)

    mock_assert.assert_awaited_once_with(db, qa_lead_id, project_id)
    assert project.default_qa_lead_user_id == qa_lead_id
    assert result is project
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_project_skips_role_check_when_field_absent():
    """A description-only update should NOT touch the role validator."""
    from app.routers.projects import update_project
    from app.models.schemas import ProjectUpdate

    project_id = uuid.uuid4()
    project = SimpleNamespace(
        id=project_id, name="P", description="old", default_qa_lead_user_id=None,
        manager_user_id=None,
    )
    db = _savepoint_capable(AsyncMock())
    db.execute = AsyncMock(return_value=_scalar(project))
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    payload = ProjectUpdate(description="new")

    with patch(
        "app.services.suite_review_service.assert_user_is_qa_lead_on_project",
        new=AsyncMock(),
    ) as mock_assert:
        await update_project(project_id, payload, db)

    mock_assert.assert_not_awaited()
    assert project.description == "new"


@pytest.mark.asyncio
async def test_update_project_400_from_validator_aborts_persist():
    """When the validator raises 400, no field assignment happens AND no
    commit fires — the project row stays unchanged."""
    from app.routers.projects import update_project
    from app.models.schemas import ProjectUpdate

    project_id = uuid.uuid4()
    original_name = "Original"
    project = SimpleNamespace(
        id=project_id, name=original_name, description=None,
        default_qa_lead_user_id=None, manager_user_id=None,
    )
    db = _savepoint_capable(AsyncMock())
    db.execute = AsyncMock(return_value=_scalar(project))
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    payload = ProjectUpdate(
        name="New name",
        default_qa_lead_user_id=uuid.uuid4(),
    )

    with patch(
        "app.services.suite_review_service.assert_user_is_qa_lead_on_project",
        new=AsyncMock(side_effect=HTTPException(status_code=400, detail="not a QA_LEAD")),
    ):
        with pytest.raises(HTTPException) as exc:
            await update_project(project_id, payload, db)

    assert exc.value.status_code == 400
    # The validator raises BEFORE the for-loop that applies updates, so
    # the in-memory object must still carry the original values.
    assert project.name == original_name
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_project_returns_404_for_missing_project():
    """Sanity: the 404 path runs before any validator call."""
    from app.routers.projects import update_project
    from app.models.schemas import ProjectUpdate

    db = _savepoint_capable(AsyncMock())
    db.execute = AsyncMock(return_value=_scalar(None))

    with patch(
        "app.services.suite_review_service.assert_user_is_qa_lead_on_project",
        new=AsyncMock(),
    ) as mock_assert:
        with pytest.raises(HTTPException) as exc:
            await update_project(
                uuid.uuid4(),
                ProjectUpdate(default_qa_lead_user_id=uuid.uuid4()),
                db,
            )

    assert exc.value.status_code == 404
    mock_assert.assert_not_awaited()


# ── POST /projects ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_project_runs_role_check_when_default_qa_lead_set():
    """POST creates the row + the creator's ProjectMember, then validates
    the default QA lead. The validator must fire exactly once."""
    from app.routers.projects import create_project
    from app.models.schemas import ProjectCreate

    qa_lead_id = uuid.uuid4()
    payload = ProjectCreate(
        name="New Project",
        slug="new-project",
        default_qa_lead_user_id=qa_lead_id,
    )
    # No project exists with that slug → conflict check returns None.
    db = _savepoint_capable(AsyncMock())
    db.execute = AsyncMock(return_value=_scalar(None))
    db.add = MagicMock()

    # ``db.flush`` is awaited at least twice (once for project + member,
    # once before the validator runs). AsyncMock handles repeated awaits.
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    current_user = SimpleNamespace(id=uuid.uuid4(), role="ADMIN")

    with patch(
        "app.services.suite_review_service.assert_user_is_qa_lead_on_project",
        new=AsyncMock(return_value=None),
    ) as mock_assert, patch(
        "app.core.deps.invalidate_membership_cache",
        new=AsyncMock(return_value=None),
    ):
        result = await create_project(payload, db, current_user)

    mock_assert.assert_awaited_once()
    # Validator was called with the right user_id.
    assert mock_assert.await_args.args[1] == qa_lead_id
    assert result is not None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_project_skips_role_check_when_default_qa_lead_omitted():
    """A bare create without ``default_qa_lead_user_id`` must NOT call the
    validator — the field is optional at create time."""
    from app.routers.projects import create_project
    from app.models.schemas import ProjectCreate

    payload = ProjectCreate(name="Bare Project", slug="bare-project")
    db = _savepoint_capable(AsyncMock())
    db.execute = AsyncMock(return_value=_scalar(None))
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    current_user = SimpleNamespace(id=uuid.uuid4(), role="ADMIN")

    with patch(
        "app.services.suite_review_service.assert_user_is_qa_lead_on_project",
        new=AsyncMock(),
    ) as mock_assert, patch(
        "app.core.deps.invalidate_membership_cache",
        new=AsyncMock(return_value=None),
    ):
        await create_project(payload, db, current_user)

    mock_assert.assert_not_awaited()
