"""Regression coverage for consistent global-search date filtering.

The public ``days`` filter is forwarded to every entity adapter. Suites,
flaky tests, and releases previously accepted the resulting boundary but
ignored it, producing a mixed response where only half the entity types were
limited to the requested period.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("sqlalchemy")

from app.services import global_search_service as svc  # noqa: E402


class _EmptyResult:
    def all(self):
        return []

    def scalars(self):
        return self


class _CapturingDB:
    def __init__(self):
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _EmptyResult()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter", "expected_column"),
    [
        (svc._search_suites, "test_cases.created_at"),
        (svc._search_flaky_tests, "test_case_history.created_at"),
        (svc._search_releases, "releases.created_at"),
    ],
    ids=["suites", "flaky-tests", "releases"],
)
async def test_global_search_adapter_applies_period_boundary(adapter, expected_column):
    db = _CapturingDB()
    boundary = datetime(2026, 8, 1, tzinfo=timezone.utc)

    await adapter(db, "auth", project_id=None, period_start=boundary)

    assert db.statement is not None
    sql = str(db.statement)
    assert f"{expected_column} >=" in sql
