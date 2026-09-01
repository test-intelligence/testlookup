"""Unit tests for ``test_management_service`` managed-case suite-FK wiring
(migration 0087).

Authored / AI-generated cases historically carried only a free-text
``suite_name``. As of migration 0087 they also carry a structured FK
``test_suite_id`` to ``test_suites``. These tests pin:

  * Create with ``suite_name`` set → service resolves-or-creates the
    matching ``TestSuite`` row and stamps the FK on the new case.
  * Create without ``suite_name`` → FK stays NULL (no implicit suite).
  * Create with whitespace-only ``suite_name`` → treated as empty.
  * Update changes ``suite_name`` → ``test_suite_id`` is updated in
    lockstep (resolve-or-create under the case's *own* project — never
    cross-project).
  * Update clears ``suite_name`` to empty/null → ``test_suite_id`` is
    cleared.
  * Update without touching ``suite_name`` → FK is preserved.
  * Update with ``suite_name`` unchanged still resolves the suite (same
    code path; the resolver returns the existing row).

The DB layer uses the same ``AsyncMock`` + ``FakeExecuteResult``
pattern as ``tests/services/test_test_suite_service.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.schemas import ManagedTestCaseCreate, ManagedTestCaseUpdate
from app.services import test_management_service as svc
from tests.conftest import FakeExecuteResult


@pytest.fixture(autouse=True)
def _allow_project_access(monkeypatch):
    """create_managed_test_case now enforces project membership via
    resolve_project_scope (cross-tenant IDOR fix). These tests exercise the
    suite-FK wiring, not access control, so permit access for all of them."""
    monkeypatch.setattr(
        "app.core.deps.resolve_project_scope",
        AsyncMock(return_value=(None, None)),
    )


def _fake_project(project_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(id=project_id, name="Acme")


def _fake_user(user_id: uuid.UUID | None = None) -> SimpleNamespace:
    # ``audit_event`` reads ``full_name`` (or falls back to ``username``)
    # off the actor — supply both so the create path doesn't blow up
    # on AttributeError before the assertion runs.
    return SimpleNamespace(
        id=user_id or uuid.uuid4(),
        full_name="Test User",
        username="testuser",
    )


# ── create_managed_test_case ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_resolves_existing_suite_and_stamps_fk():
    """When ``suite_name`` matches an existing TestSuite for the project,
    the service uses that suite's id — no new row inserted."""
    project_id = uuid.uuid4()
    existing_suite = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        name="Regression",
    )

    db = AsyncMock()
    db.get = AsyncMock(return_value=_fake_project(project_id))
    # First execute: get_or_create_suite_by_name SELECTs the existing suite.
    db.execute = AsyncMock(return_value=FakeExecuteResult(scalar_value=existing_suite))
    db.flush = AsyncMock()
    added: list = []
    db.add = lambda obj: added.append(obj)

    payload = ManagedTestCaseCreate(
        project_id=project_id,
        title="Verify login",
        suite_name="Regression",
    )
    result = await svc.create_managed_test_case(db, payload, _fake_user())

    assert result.test_suite_id == existing_suite.id, (
        "create must stamp the structured FK from the resolved suite"
    )
    # Only the test case + the version row are added — not a duplicate suite.
    types_added = [type(o).__name__ for o in added]
    assert types_added.count("TestSuite") == 0
    assert types_added.count("ManagedTestCase") == 1


@pytest.mark.asyncio
async def test_create_without_suite_name_leaves_fk_null():
    """Cases created without a suite_name keep ``test_suite_id`` NULL —
    the legacy authored-case path keeps working."""
    project_id = uuid.uuid4()

    db = AsyncMock()
    db.get = AsyncMock(return_value=_fake_project(project_id))
    db.execute = AsyncMock()  # must NOT be called — no suite to resolve
    db.flush = AsyncMock()
    added: list = []
    db.add = lambda obj: added.append(obj)

    payload = ManagedTestCaseCreate(project_id=project_id, title="No suite")
    result = await svc.create_managed_test_case(db, payload, _fake_user())

    assert result.test_suite_id is None
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_with_whitespace_suite_name_treated_as_empty():
    """``suite_name='   '`` is functionally empty — must not trigger a
    suite resolve and must leave the FK NULL. Otherwise we'd materialise
    a TestSuite whose name is whitespace."""
    project_id = uuid.uuid4()

    db = AsyncMock()
    db.get = AsyncMock(return_value=_fake_project(project_id))
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    db.add = lambda obj: None

    payload = ManagedTestCaseCreate(
        project_id=project_id,
        title="Whitespace suite",
        suite_name="   ",
    )
    result = await svc.create_managed_test_case(db, payload, _fake_user())
    assert result.test_suite_id is None
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_404s_when_project_missing():
    """Pre-existing contract — keep it green after the suite-resolution
    pre-step lands. Project guard fires BEFORE any suite-resolution side
    effects, so a stale activeProjectId in the FE doesn't accidentally
    create a TestSuite under a non-existent project."""
    from fastapi import HTTPException

    project_id = uuid.uuid4()
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    db.execute = AsyncMock()

    payload = ManagedTestCaseCreate(
        project_id=project_id,
        title="orphan",
        suite_name="ShouldNotBeCreated",
    )
    with pytest.raises(HTTPException) as exc_info:
        await svc.create_managed_test_case(db, payload, _fake_user())
    assert exc_info.value.status_code == 404
    # Crucially: no suite resolution attempted.
    db.execute.assert_not_awaited()


# ── update_managed_test_case ────────────────────────────────────────────────


def _existing_case(project_id: uuid.UUID, **overrides) -> SimpleNamespace:
    base = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        title="old",
        description=None,
        steps=None,
        # Rich-detail authored parameter definitions. The update path
        # snapshots this onto the new TestCaseVersion, so the stub must
        # carry the attribute exactly as the real ManagedTestCase model does.
        parameters=None,
        expected_result=None,
        status="draft",
        version=1,
        suite_name="OldSuite",
        test_suite_id=None,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


@pytest.mark.asyncio
async def test_update_rename_suite_resolves_within_same_project(monkeypatch):
    """Renaming the suite resolves-or-creates the new TestSuite under
    the case's own project. The FK is updated in lockstep."""
    project_id = uuid.uuid4()
    existing = _existing_case(project_id, test_suite_id=uuid.uuid4())
    new_suite = SimpleNamespace(id=uuid.uuid4(), project_id=project_id, name="Smoke")

    # Stub get_test_case_or_404 to skip the DB load.
    monkeypatch.setattr(svc, "get_test_case_or_404", AsyncMock(return_value=existing))
    # Stub audit_event (writes its own row we don't care about here).
    monkeypatch.setattr(svc, "audit_event", AsyncMock())

    db = AsyncMock()
    # First execute call: get_or_create_suite_by_name SELECTs the new suite.
    db.execute = AsyncMock(return_value=FakeExecuteResult(scalar_value=new_suite))
    db.flush = AsyncMock()
    db.add = lambda obj: None

    payload = ManagedTestCaseUpdate(suite_name="Smoke")
    result = await svc.update_managed_test_case(
        db, existing.id, payload, _fake_user()
    )

    assert result.suite_name == "Smoke"
    assert result.test_suite_id == new_suite.id, (
        "rename must move the structured anchor too — otherwise the "
        "free-text label and the FK silently disagree"
    )


@pytest.mark.asyncio
async def test_update_clear_suite_name_clears_fk(monkeypatch):
    """Clearing ``suite_name`` to empty/null also clears the FK so the
    two columns can't drift."""
    project_id = uuid.uuid4()
    existing = _existing_case(project_id, suite_name="Regression", test_suite_id=uuid.uuid4())

    monkeypatch.setattr(svc, "get_test_case_or_404", AsyncMock(return_value=existing))
    monkeypatch.setattr(svc, "audit_event", AsyncMock())

    db = AsyncMock()
    db.execute = AsyncMock()  # must NOT be called — empty name doesn't resolve
    db.flush = AsyncMock()
    db.add = lambda obj: None

    payload = ManagedTestCaseUpdate(suite_name="")
    result = await svc.update_managed_test_case(
        db, existing.id, payload, _fake_user()
    )

    assert result.suite_name == ""
    assert result.test_suite_id is None
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_unrelated_field_preserves_fk(monkeypatch):
    """Updates that don't touch ``suite_name`` leave the FK alone — we
    don't want a title change to accidentally null out the anchor."""
    project_id = uuid.uuid4()
    pinned_fk = uuid.uuid4()
    existing = _existing_case(project_id, suite_name="Regression", test_suite_id=pinned_fk)

    monkeypatch.setattr(svc, "get_test_case_or_404", AsyncMock(return_value=existing))
    monkeypatch.setattr(svc, "audit_event", AsyncMock())

    db = AsyncMock()
    db.execute = AsyncMock()  # must NOT be called
    db.flush = AsyncMock()
    db.add = lambda obj: None

    payload = ManagedTestCaseUpdate(title="Brand new title")
    result = await svc.update_managed_test_case(
        db, existing.id, payload, _fake_user()
    )

    assert result.title == "Brand new title"
    assert result.test_suite_id == pinned_fk
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_snapshots_parameters_into_version(monkeypatch):
    """The version row written on update must snapshot the case's rich-detail
    ``parameters``. Regression: the update path reads ``test_case.parameters``,
    so a case (or stub) lacking that attribute crashes the whole update."""
    project_id = uuid.uuid4()
    authored_params = [{"name": "env", "value": "staging", "sensitive": False}]
    existing = _existing_case(
        project_id, suite_name="Regression", test_suite_id=uuid.uuid4(),
        parameters=authored_params,
    )

    monkeypatch.setattr(svc, "get_test_case_or_404", AsyncMock(return_value=existing))
    monkeypatch.setattr(svc, "audit_event", AsyncMock())

    added: list = []
    db = AsyncMock()
    db.execute = AsyncMock()  # unrelated field → resolver not called
    db.flush = AsyncMock()
    db.add = added.append

    payload = ManagedTestCaseUpdate(title="Renamed")
    await svc.update_managed_test_case(db, existing.id, payload, _fake_user())

    version_rows = [o for o in added if type(o).__name__ == "TestCaseVersion"]
    assert version_rows, "update must write a TestCaseVersion snapshot"
    assert version_rows[-1].parameters == authored_params
