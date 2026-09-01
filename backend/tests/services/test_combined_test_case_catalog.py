"""Bounded SQL-union coverage for authored plus automation catalog paging."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.test_management_service import list_combined_test_case_identities


@pytest.mark.asyncio
async def test_combined_catalog_pages_beyond_1000_with_filters_in_sql():
    project_id = uuid.uuid4()
    page_ids = [uuid.uuid4() for _ in range(200)]
    count_result = SimpleNamespace(scalar=lambda: 1205)
    rows_result = SimpleNamespace(
        all=lambda: [("managed", case_id) for case_id in page_ids]
    )
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[count_result, rows_result])
    )

    identities, total, pages = await list_combined_test_case_identities(
        db,
        project_id,
        include_archived=True,
        test_type="automation",
        search="checkout",
        suite_name="Regression",
        page=6,
        size=200,
    )

    assert identities == [("managed", case_id) for case_id in page_ids]
    assert total == 1205
    assert pages == 7

    count_sql = str(db.execute.await_args_list[0].args[0].compile()).lower()
    page_statement = db.execute.await_args_list[1].args[0]
    page_sql = str(page_statement.compile()).lower()
    assert "union all" in count_sql
    assert "managed_test_cases.test_type" in count_sql
    assert "lower(trim(managed_test_cases.suite_name))" in count_sql
    assert "managed_test_cases.title" in count_sql
    assert "test_cases.test_name" in count_sql
    assert "limit" in page_sql and "offset" in page_sql
    params = page_statement.compile().params
    assert 200 in params.values()
    assert 1000 in params.values()
    assert "1000" not in page_sql


@pytest.mark.asyncio
async def test_combined_catalog_dedup_is_project_scoped_and_excludes_linked_fingerprint():
    project_id = uuid.uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalar=lambda: 0),
                SimpleNamespace(all=lambda: []),
            ]
        )
    )

    assert await list_combined_test_case_identities(
        db,
        project_id,
        include_archived=False,
        test_type=None,
        search=None,
        suite_name=None,
        page=1,
        size=25,
    ) == ([], 0, 0)

    sql = str(db.execute.await_args_list[0].args[0].compile()).lower()
    assert "managed_test_cases.project_id" in sql
    assert "managed_test_cases.test_fingerprint" in sql
    assert "test_runs.project_id" in sql
    assert "not (exists" in sql or "not exists" in sql
