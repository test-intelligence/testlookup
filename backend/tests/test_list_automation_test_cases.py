"""Unit tests for ``list_automation_test_cases``.

User-reported regression:
    /test-management?tab=Test+Cases rendered an empty table for admins
    landing in All-Projects mode (the new default after commit 7be8193).
    Root cause: the router gated the automation-merge lookup with
    ``if project_id is not None`` and the service required a UUID,
    so admins in All-Projects mode saw only ``managed_test_cases`` rows
    (often empty in fresh deployments) — every per-run automation test
    was hidden.

This file pins:
1. The project-scoped path still filters by project and stamps each
   synth row with that project_id.
2. The All-Projects path (project_id=None) drops the WHERE filter and
   each synth row reports its own ``TestRun.project_id``.
3. ``exclude_fingerprints`` continues to skip authored-case duplicates
   on both paths.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")


def _synth_row(
    *,
    project_id: uuid.UUID,
    fingerprint: str,
    test_name: str,
    suite_name: str | None = "smoke",
    class_name: str | None = "SmokeSuite",
    status: str = "PASSED",
    owner: str | None = "QA Platform",
    tags=None,
    run_created_at: datetime | None = None,
):
    """Mimic the columns SQLAlchemy returns from the subquery select in
    ``list_automation_test_cases``. All field names must match the
    ``label(...)`` aliases the function reads via ``r.<attr>``.
    """
    return SimpleNamespace(
        id=uuid.uuid4(),
        test_fingerprint=fingerprint,
        test_name=test_name,
        class_name=class_name,
        suite_name=suite_name,
        status=status,
        owner=owner,
        canonical_test_case_id=uuid.uuid4(),
        failure_category=None,
        tags=tags,
        run_created_at=run_created_at or datetime(2026, 5, 15, tzinfo=timezone.utc),
        run_id=uuid.uuid4(),
        run_project_id=project_id,
    )


class _ExecResult:
    """Minimal stand-in for SQLAlchemy ``Result`` — only ``.all()`` is used."""
    def __init__(self, rows):
        self._rows = rows
    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_project_scoped_returns_rows_stamped_with_that_project():
    from app.services.test_management_service import list_automation_test_cases

    project = uuid.uuid4()
    rows = [
        _synth_row(project_id=project, fingerprint="fp-1", test_name="test_login"),
        _synth_row(project_id=project, fingerprint="fp-2", test_name="test_logout"),
    ]
    db = SimpleNamespace(execute=AsyncMock(return_value=_ExecResult(rows)))

    result = await list_automation_test_cases(db, project_id=project)

    assert len(result) == 2
    # Every row stamped with the requested project_id (the function's
    # arg, not the row's run_project_id — these match in this case).
    assert {r["project_id"] for r in result} == {project}
    assert {r["title"] for r in result} == {"test_login", "test_logout"}
    # Synth rows are marked as automation source for the UI badge.
    assert {r["source"] for r in result} == {"automation"}
    assert all(r["is_automated"] for r in result)
    assert {r["owner"] for r in result} == {"QA Platform"}
    assert all(r["latest_run_id"] == row.run_id for r, row in zip(result, rows, strict=True))
    assert all(r["latest_test_case_id"] == row.id for r, row in zip(result, rows, strict=True))
    assert all(
        r["canonical_test_case_id"] == row.canonical_test_case_id
        for r, row in zip(result, rows, strict=True)
    )


@pytest.mark.asyncio
async def test_all_projects_returns_rows_with_their_own_project_id():
    """The regression fix: project_id=None must NOT collapse rows onto a
    single project — each row reports its TestRun.project_id."""
    from app.services.test_management_service import list_automation_test_cases

    project_a = uuid.uuid4()
    project_b = uuid.uuid4()
    rows = [
        _synth_row(project_id=project_a, fingerprint="fp-a", test_name="test_a"),
        _synth_row(project_id=project_b, fingerprint="fp-b", test_name="test_b"),
        _synth_row(project_id=project_b, fingerprint="fp-c", test_name="test_c"),
    ]
    db = SimpleNamespace(execute=AsyncMock(return_value=_ExecResult(rows)))

    result = await list_automation_test_cases(db, project_id=None)

    assert len(result) == 3
    by_title = {r["title"]: r for r in result}
    assert by_title["test_a"]["project_id"] == project_a
    assert by_title["test_b"]["project_id"] == project_b
    assert by_title["test_c"]["project_id"] == project_b
    # No row carries a literal None project_id — every synth row must
    # be routable to a real project on the frontend.
    assert all(r["project_id"] is not None for r in result)


@pytest.mark.asyncio
async def test_exclude_fingerprints_skips_authored_duplicates():
    """When the same fingerprint is already present in managed_test_cases
    (the router builds ``managed_fps`` and passes it in), the synth row
    must NOT be added to avoid double-counting in the merged list."""
    from app.services.test_management_service import list_automation_test_cases

    project = uuid.uuid4()
    rows = [
        _synth_row(project_id=project, fingerprint="dup-1", test_name="already_authored"),
        _synth_row(project_id=project, fingerprint="fp-keep", test_name="kept"),
    ]
    db = SimpleNamespace(execute=AsyncMock(return_value=_ExecResult(rows)))

    result = await list_automation_test_cases(
        db,
        project_id=project,
        exclude_fingerprints={"dup-1"},
    )

    assert [r["title"] for r in result] == ["kept"]


@pytest.mark.asyncio
async def test_empty_query_returns_empty_list():
    """No rows from the DB means an empty list, not None — callers
    ``.extend`` / ``sorted([..., *automation_dicts])`` rely on this."""
    from app.services.test_management_service import list_automation_test_cases

    db = SimpleNamespace(execute=AsyncMock(return_value=_ExecResult([])))

    result = await list_automation_test_cases(db, project_id=uuid.uuid4())

    assert result == []


@pytest.mark.asyncio
async def test_owner_and_execution_identity_survive_response_serialization():
    """The list response must not silently discard the fields the UI needs."""
    from app.models.schemas import ManagedTestCaseResponse
    from app.services.test_management_service import list_automation_test_cases

    project = uuid.uuid4()
    source = _synth_row(
        project_id=project,
        fingerprint="rich-detail-owner",
        test_name="rich_detail_production_validation",
        owner="QA Platform",
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_ExecResult([source])))

    result = await list_automation_test_cases(db, project_id=project)
    response = ManagedTestCaseResponse.model_validate(result[0])

    assert response.owner == "QA Platform"
    assert response.latest_run_id == source.run_id
    assert response.latest_test_case_id == source.id
    assert response.canonical_test_case_id == source.canonical_test_case_id
