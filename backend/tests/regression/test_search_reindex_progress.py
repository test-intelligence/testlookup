"""Regression guards for resumable, project-safe semantic reindexing."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _rows(count: int):
    return [
        SimpleNamespace(
            id=f"id-{index}",
            test_name=f"test-{index}",
            suite_name="suite",
            error_message=None,
            status="passed",
            test_run_id=f"run-{index}",
            project_id="project-a",
            created_at=None,
            step_text=None,
        )
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_incremental_upsert_checkpoints_every_completed_batch(monkeypatch):
    from app.services import semantic_search

    rows = _rows(401)
    collection = MagicMock()
    checkpoints = MagicMock()
    monkeypatch.setattr(semantic_search, "_update_cursor", checkpoints)

    count = await semantic_search._upsert_rows_to_collection(
        collection,
        rows,
        checkpoint_cursor=True,
        cursor_project_id="project-a",
    )

    assert count == 401
    assert [call.args[0][-1].id for call in checkpoints.call_args_list] == [
        "id-199",
        "id-399",
        "id-400",
    ]
    assert all(call.args[1] == "project-a" for call in checkpoints.call_args_list)


@pytest.mark.asyncio
async def test_failed_later_batch_preserves_prior_checkpoint(monkeypatch):
    from app.services import semantic_search

    rows = _rows(401)
    collection = MagicMock()
    collection.upsert.side_effect = [None, RuntimeError("embedding timeout")]
    checkpoints = MagicMock()
    monkeypatch.setattr(semantic_search, "_update_cursor", checkpoints)

    with pytest.raises(RuntimeError, match="embedding timeout"):
        await semantic_search._upsert_rows_to_collection(
            collection,
            rows,
            checkpoint_cursor=True,
            cursor_project_id="project-a",
        )

    checkpoints.assert_called_once()
    assert checkpoints.call_args.args[0][-1].id == "id-199"


def test_project_cursor_keys_cannot_advance_the_global_cursor():
    from app.services.semantic_search import (
        _REDIS_CURSOR_KEY,
        _REDIS_TIMESTAMP_KEY,
        _cursor_keys,
    )

    assert _cursor_keys() == (_REDIS_CURSOR_KEY, _REDIS_TIMESTAMP_KEY)
    project_keys = _cursor_keys("project-a")
    assert project_keys[0] != _REDIS_CURSOR_KEY
    assert project_keys[1] != _REDIS_TIMESTAMP_KEY
    assert project_keys[0].endswith(":project:project-a")


def test_cursor_predicate_keeps_equal_timestamp_rows_after_the_exact_id():
    from app.services.semantic_search import _after_incremental_cursor

    sql = str(_after_incremental_cursor(uuid.uuid4()))

    assert "test_cases.created_at >" in sql
    assert "test_cases.created_at =" in sql
    assert "test_cases.id >" in sql


def test_missing_cursor_row_restarts_instead_of_filtering_everything():
    from app.services.semantic_search import _after_incremental_cursor

    sql = str(_after_incremental_cursor(uuid.uuid4())).lower()

    assert "coalesce" in sql


@pytest.mark.asyncio
async def test_full_reindex_orders_rows_before_persisting_the_last_cursor(monkeypatch):
    from app.services import semantic_search

    rows = _rows(2)
    captured = {}

    class _Result:
        def all(self):
            return rows

    class _DB:
        async def execute(self, statement):
            captured["statement"] = statement
            return _Result()

    monkeypatch.setattr(
        semantic_search,
        "_get_or_create_collection",
        AsyncMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        semantic_search,
        "_upsert_rows_to_collection",
        AsyncMock(return_value=2),
    )
    update_cursor = MagicMock()
    monkeypatch.setattr(semantic_search, "_update_cursor", update_cursor)

    assert await semantic_search.index_test_cases(_DB()) == 2

    sql = str(captured["statement"])
    assert "ORDER BY test_cases.created_at ASC, test_cases.id ASC" in sql
    update_cursor.assert_called_once_with(rows, None)
