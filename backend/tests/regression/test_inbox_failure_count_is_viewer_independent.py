"""D-38: the Inbox "xN" chip must not depend on who is looking.

The per-test count reused the list's filters, which carry the viewer's scope
(``assigned_to_user_id`` in ``mine``, ``triage_status = PENDING_REVIEW``). So the
same failing test read "x9" for the lead and "x1" for its assignee. The count
now keeps only the shared axes: window, project, live project, release, suite.

This drives the real handler with a mocked session and reads the SQL of the
grouping statement it sends. The Postgres proof (9 for both viewers) is
``tests/integration/test_inbox_counts_agree_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.regression


def _result(*, scalar=None, rows=None):
    res = MagicMock()
    res.scalar = MagicMock(return_value=scalar)
    res.all = MagicMock(return_value=rows or [])
    return res


def _row(project_id):
    return SimpleNamespace(
        id=uuid.uuid4(), test_name="t_repeat", suite_name="Checkout", class_name="C",
        status="FAILED", severity=None, failure_category=None, error_message=None,
        duration_ms=None, created_at=datetime.now(timezone.utc), test_run_id=uuid.uuid4(),
        canonical_test_case_id=None, build_number=None, project_id=project_id,
        project_name="P", triage_status="PENDING_REVIEW", triage_notes=None,
        assigned_to_user_id=None, stack_trace=None,
    )


async def _statements(scope: str) -> list:
    from app.routers.my_failures import list_my_assigned_failures

    project_id = uuid.uuid4()
    sent: list = []
    results = iter([
        _result(scalar=1),
        _result(rows=[_row(project_id)]),
        _result(rows=[]),
    ])

    async def _execute(stmt, *a, **k):
        sent.append(stmt)
        return next(results)

    db = MagicMock()
    db.execute = _execute
    user = SimpleNamespace(id=uuid.uuid4(), role="QA_LEAD")
    with patch("app.services.runs_service.fetch_run_seq_map", AsyncMock(return_value={})), \
            patch("app.services.runs_service.first_failed_step_by_canonical",
                  AsyncMock(return_value={})), \
            patch("app.services.codeowners_service.codeowners_reasons_for_rows",
                  AsyncMock(return_value={})), \
            patch("app.core.deps.resolve_project_scope",
                  AsyncMock(return_value=(project_id, {project_id}))):
        await list_my_assigned_failures(
            project_id=str(project_id), days=7, release_id=None, suite_name=None,
            page=1, size=25, scope=scope, db=db, current_user=user,
        )
    return sent


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["mine", "team"])
async def test_the_per_test_count_ignores_the_viewers_scope(scope):
    total, page, grouping = await _statements(scope)
    where = _sql(grouping).split("WHERE", 1)[1]
    assert "assigned_to_user_id" not in where, "the xN count still depends on the assignee"
    assert "triage_status" not in where, "the xN count still drops triaged failures"
    # ...and it keeps the shared axes.
    assert "created_at" in where and "project_id" in where
    # The rows themselves are still the viewer's.
    assert "triage_status" in _sql(total).split("WHERE", 1)[1]
    if scope == "mine":
        assert "assigned_to_user_id" in _sql(page).split("WHERE", 1)[1]
