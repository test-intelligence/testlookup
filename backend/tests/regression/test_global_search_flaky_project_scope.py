"""Regression: global-search flaky adapter blended fingerprints across projects.

Bug pinned (review/global-search-service, 2026-06-02):

``_search_flaky_tests`` did ``GROUP BY test_fingerprint`` only. Since
``make_test_fingerprint`` is not project-salted, a same-named test in two
projects (reachable in fan-out mode across a user's memberships, or unrestricted
for an admin) collapsed into ONE flaky entry with summed runs + a ``func.max``
name from an arbitrary project. Fix: group by ``(test_fingerprint, project_id)``
so each project's flaky test is its own correctly-scoped entry, and surface the
``project_id`` on the result.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from app.services import global_search_service as svc  # noqa: E402


class _Res:
    def __init__(self, rows=None):
        self._rows = rows or []

    def all(self):
        return self._rows


class _CapturingDB:
    def __init__(self):
        self.sql: list[str] = []

    async def execute(self, stmt, *a, **k):
        self.sql.append(str(stmt))
        return _Res(rows=[])


@pytest.mark.asyncio
async def test_flaky_search_groups_by_project_id():
    db = _CapturingDB()
    await svc._search_flaky_tests(
        db, "login", project_id=None, period_start=None,
        allowed_project_ids={uuid.uuid4(), uuid.uuid4()},
    )
    assert db.sql, "no query executed"
    sql = db.sql[0]
    # The GROUP BY must include BOTH the fingerprint and the project so distinct
    # projects' same-named tests aren't merged.
    assert "GROUP BY" in sql
    assert "test_fingerprint" in sql
    assert "test_runs.project_id" in sql


@pytest.mark.asyncio
async def test_flaky_result_carries_project_id():
    """The flaky result dict surfaces project_id so a fan-out result is
    attributable to a project (previously absent)."""
    from types import SimpleNamespace

    pid = uuid.uuid4()
    row = SimpleNamespace(
        test_fingerprint="fp1", project_id=pid,
        test_name="test_login", suite_name="auth",
        total_runs=20, fail_count=9,  # 45% → flaky range
    )

    class _RowDB:
        async def execute(self, *a, **k):
            return _Res(rows=[row])

    results = await svc._search_flaky_tests(
        _RowDB(), "login", project_id=str(pid), period_start=None,
    )
    assert len(results) == 1
    assert results[0]["entity_type"] == "flaky_test"
    assert results[0]["project_id"] == str(pid)
    assert results[0]["metadata"]["failure_rate"] == 45.0
