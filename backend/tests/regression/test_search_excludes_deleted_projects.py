"""Regression: every Search mode must exclude soft-deleted projects."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql


def _sql(statement) -> str:
    """Render a statement for inspection.

    ``literal_binds`` is attempted first because it reads better, but the
    keyword-search statements are now built once per query shape with NAMED
    bind parameters that carry no value until execution -- inlining those
    raises. The assertions here are about structure (the projects join and the
    is_active predicate), which survives either rendering.
    """
    try:
        return str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        ).lower()
    except Exception:
        return str(statement.compile(dialect=postgresql.dialect())).lower()


def _assert_active_project_filter(statement) -> None:
    sql = _sql(statement)
    assert "join projects" in sql
    assert "projects.is_active is true" in sql


@pytest.mark.asyncio
async def test_keyword_search_filters_deleted_projects_in_results_and_count():
    from app.services.search_service import search_test_cases_query

    captured = []
    rows_result = MagicMock()
    rows_result.all.return_value = []
    count_result = MagicMock()
    count_result.scalar.return_value = 0

    async def execute(statement, params=None):
        # ``params`` arrived when the statements became cached-and-reused: every
        # value now travels as a bind parameter instead of being built into the
        # statement. The fake has to accept it, but this test is unchanged in
        # what it asserts -- both statements must still join projects and filter
        # on is_active.
        captured.append(statement)
        return rows_result if len(captured) == 1 else count_result

    db = SimpleNamespace(execute=execute)
    assert await search_test_cases_query(db, q="login", page=1, size=20) == (
        [],
        0,
        0,
    )
    assert len(captured) == 2
    for statement in captured:
        _assert_active_project_filter(statement)


@pytest.mark.asyncio
@pytest.mark.parametrize("indexer_name", ["index_test_cases", "index_incremental"])
async def test_semantic_indexers_skip_deleted_projects(indexer_name):
    from app.services import semantic_search

    captured = []
    result = MagicMock()
    result.all.return_value = []

    async def execute(statement):
        captured.append(statement)
        return result

    db = SimpleNamespace(execute=execute)
    collection = MagicMock()
    with (
        patch.object(
            semantic_search,
            "_get_or_create_collection",
            AsyncMock(return_value=collection),
        ),
        patch.object(semantic_search, "_get_redis", return_value=MagicMock()),
    ):
        assert await getattr(semantic_search, indexer_name)(db) == 0

    assert len(captured) == 1
    _assert_active_project_filter(captured[0])


@pytest.mark.asyncio
async def test_semantic_result_revalidation_excludes_deleted_projects():
    from app.services import semantic_search

    test_case_id = uuid.uuid4()
    collection = MagicMock()
    collection.query.return_value = {
        "ids": [[str(test_case_id)]],
        "distances": [[0.1]],
        "metadatas": [[{}]],
    }
    result = MagicMock()
    result.all.return_value = []
    captured = []

    async def execute(statement):
        captured.append(statement)
        return result

    with patch.object(
        semantic_search,
        "_get_or_create_collection",
        AsyncMock(return_value=collection),
    ):
        assert await semantic_search.semantic_search(
            SimpleNamespace(execute=execute), q="login", page=1, size=20
        ) == ([], 0, 0)

    assert len(captured) == 1
    _assert_active_project_filter(captured[0])


@pytest.mark.asyncio
async def test_all_global_search_adapters_exclude_deleted_projects():
    from app.services import global_search_service

    captured = []
    result = MagicMock()
    result.all.return_value = []
    result.scalars.return_value.all.return_value = []

    async def execute(statement):
        captured.append(statement)
        return result

    db = SimpleNamespace(execute=execute)
    for adapter in global_search_service._ADAPTERS.values():
        assert await adapter(db, "login", None, None) == []

    assert len(captured) == len(global_search_service._ADAPTERS) == 6
    for statement in captured:
        _assert_active_project_filter(statement)
