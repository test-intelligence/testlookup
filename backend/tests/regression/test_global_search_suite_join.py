"""Regression coverage for the global-search suite SQL join root."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

from app.services.global_search_service import _search_suites  # noqa: E402


@pytest.mark.asyncio
async def test_suite_search_compiles_test_case_to_test_run_join():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=type("Rows", (), {"all": lambda self: []})())

    await _search_suites(db, "auth", None, None)

    statement = db.execute.await_args.args[0]
    sql = str(statement)
    assert "JOIN test_runs" in sql
    assert "test_cases" in sql

