"""Unit tests for ``services.test_suite_service`` (Phase 2 of the TestSuite
+ CanonicalTestCase feature).

The DB-bound paths are exercised with ``AsyncMock`` sessions plus
``FakeExecuteResult`` (see ``tests/conftest.py``) — same approach as
``tests/services/test_run_compare_service.py`` and the rest of the
non-integration service suite.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import test_suite_service as svc
from tests.conftest import FakeExecuteResult


# ── default_suite_name_for ────────────────────────────────────────────────


def test_default_suite_name_format():
    assert svc.default_suite_name_for("Acme") == "Default Suite (Acme)"
    assert svc.default_suite_name_for("acme corp") == "Default Suite (acme corp)"


# ── get_or_create_default_suite ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_or_create_default_suite_returns_existing():
    project = SimpleNamespace(id=uuid.uuid4(), name="Acme")
    existing_suite = SimpleNamespace(
        id=uuid.uuid4(), project_id=project.id, name="Default Suite (Acme)", is_default=True
    )

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeExecuteResult(scalar_value=existing_suite))

    result = await svc.get_or_create_default_suite(db, project)

    assert result is existing_suite
    db.add.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_get_or_create_default_suite_creates_when_missing():
    project = SimpleNamespace(id=uuid.uuid4(), name="Acme")

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeExecuteResult(scalar_value=None))
    db.flush = AsyncMock()

    result = await svc.get_or_create_default_suite(db, project)

    assert result.is_default is True
    assert result.name == "Default Suite (Acme)"
    assert result.project_id == project.id
    db.add.assert_called_once()
    db.flush.assert_awaited_once()


# ── get_or_create_suite_by_name ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_or_create_suite_by_name_returns_existing():
    project_id = uuid.uuid4()
    existing = SimpleNamespace(id=uuid.uuid4(), project_id=project_id, name="Regression")

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeExecuteResult(scalar_value=existing))

    result = await svc.get_or_create_suite_by_name(db, project_id, "Regression")
    assert result is existing
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_get_or_create_suite_by_name_creates_new():
    project_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeExecuteResult(scalar_value=None))
    db.flush = AsyncMock()

    result = await svc.get_or_create_suite_by_name(db, project_id, "Smoke")
    assert result.name == "Smoke"
    assert result.is_default is False
    db.add.assert_called_once()


# ── sync_canonical_test_cases ───────────────────────────────────────────


def _make_test_case(fingerprint: str, suite_name: str | None, test_name: str = "test_x"):
    tc = SimpleNamespace(
        test_fingerprint=fingerprint,
        suite_name=suite_name,
        test_name=test_name,
        class_name="com.example.MyTest",
        canonical_test_case_id=None,
    )
    return tc


@pytest.mark.asyncio
async def test_sync_canonical_no_cases_returns_zero_counts():
    empty_result = FakeExecuteResult()
    empty_result.scalars = lambda: SimpleNamespace(all=lambda: [])

    db = AsyncMock()
    db.execute = AsyncMock(return_value=empty_result)

    result = await svc.sync_canonical_test_cases(db, uuid.uuid4(), uuid.uuid4())
    assert result == {"added": 0, "updated": 0, "linked": 0, "skipped": 0}


@pytest.mark.asyncio
async def test_sync_canonical_adds_new_fingerprints(monkeypatch):
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    suite = SimpleNamespace(id=uuid.uuid4(), project_id=project_id, name="Regression")

    cases = [
        _make_test_case("fp1", "Regression", "test_a"),
        _make_test_case("fp2", "Regression", "test_b"),
    ]

    # Three execute() calls happen in order:
    #   1) SELECT TestCase WHERE test_run_id == run_id
    #   2) SELECT TestSuite WHERE project_id == ... AND name IN (...)
    #   3) SELECT CanonicalTestCase WHERE project_id == ... AND fingerprint IN (...)
    cases_result = FakeExecuteResult()
    cases_result.scalars = lambda: SimpleNamespace(all=lambda: cases)

    suites_result = FakeExecuteResult()
    suites_result.scalars = lambda: SimpleNamespace(all=lambda: [suite])

    canonical_result = FakeExecuteResult()
    canonical_result.scalars = lambda: SimpleNamespace(all=lambda: [])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[cases_result, suites_result, canonical_result])

    # Real SQLAlchemy populates ``id`` from ``default=uuid.uuid4`` at flush
    # time. The AsyncMock flush is a no-op, so we simulate that here by
    # stamping an id on whatever was just added(). Without this, the link
    # counter under test stays at zero because ``canonical.id`` is None.
    def _stamp_ids(*_args, **_kwargs):
        for call in db.add.call_args_list:
            obj = call.args[0]
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    db.flush = AsyncMock(side_effect=_stamp_ids)

    result = await svc.sync_canonical_test_cases(db, project_id, run_id)

    assert result["added"] == 2
    assert result["updated"] == 0
    assert result["linked"] == 2
    assert db.add.call_count == 2
    for tc in cases:
        assert tc.canonical_test_case_id is not None


@pytest.mark.asyncio
async def test_sync_canonical_updates_existing_and_restores_deleted():
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    suite = SimpleNamespace(id=uuid.uuid4(), project_id=project_id, name="Regression")
    existing_canonical = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        test_suite_id=suite.id,
        test_fingerprint="fp1",
        test_name="old_name",
        class_name="OldClass",
        status="deleted",
        deleted_at_run_id=uuid.uuid4(),
        last_seen_run_id=uuid.uuid4(),
    )
    case = _make_test_case("fp1", "Regression", test_name="new_name")
    case.class_name = "NewClass"

    cases_result = FakeExecuteResult()
    cases_result.scalars = lambda: SimpleNamespace(all=lambda: [case])
    suites_result = FakeExecuteResult()
    suites_result.scalars = lambda: SimpleNamespace(all=lambda: [suite])
    canonical_result = FakeExecuteResult()
    canonical_result.scalars = lambda: SimpleNamespace(all=lambda: [existing_canonical])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[cases_result, suites_result, canonical_result])

    result = await svc.sync_canonical_test_cases(db, project_id, run_id)

    assert result["updated"] == 1
    assert result["added"] == 0
    assert result["linked"] == 1
    # Restored.
    assert existing_canonical.status == "active"
    assert existing_canonical.deleted_at_run_id is None
    assert existing_canonical.last_seen_run_id == run_id
    # Name + class refreshed from the payload.
    assert existing_canonical.test_name == "new_name"
    assert existing_canonical.class_name == "NewClass"
    # Run TestCase linked.
    assert case.canonical_test_case_id == existing_canonical.id


@pytest.mark.asyncio
async def test_sync_canonical_skips_cases_with_no_fingerprint():
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    case = _make_test_case("", "Regression")  # empty fingerprint

    cases_result = FakeExecuteResult()
    cases_result.scalars = lambda: SimpleNamespace(all=lambda: [case])
    suites_result = FakeExecuteResult()
    suites_result.scalars = lambda: SimpleNamespace(all=lambda: [
        SimpleNamespace(id=uuid.uuid4(), name="Regression")
    ])
    canonical_result = FakeExecuteResult()
    canonical_result.scalars = lambda: SimpleNamespace(all=lambda: [])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[cases_result, suites_result, canonical_result])

    result = await svc.sync_canonical_test_cases(db, project_id, run_id)
    assert result["skipped"] == 1
    assert result["added"] == 0
