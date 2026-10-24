"""W12 SRCH-01: Similar failures lists FAILURES and says when the index is down.

``GET /search/similar/{id}`` used to return every semantic neighbour -- PASSED
results read as "similar failures" -- and called the index without
``raise_on_provider_error``, so an outage read as "nothing similar". Now it
asks for ``min(limit * 4, 40)`` candidates, keeps FAILED / BROKEN only (the
source excluded, at most ``limit``, relevance order) and answers
``semantic_status``: ``ok`` / ``not_configured`` / ``unavailable``.

The IDOR rule and the early-return dicts are pinned unedited elsewhere
(``tests/regression/test_search_similar_scope.py``,
``tests/test_exploratory_m09_search_retrieval.py``).
"""
from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.search_vocab import SIMILAR_SEMANTIC_STATUSES

SOURCE = uuid.uuid4()
PROJECT = uuid.uuid4()
TEST_NAME = "zq-similar-sentinel test_checkout_total"
ERROR_TEXT = "AssertionError: zq-error-sentinel expected 3"


class _Result:
    def first(self):
        return SimpleNamespace(test_name=TEST_NAME, error_message=ERROR_TEXT, project_id=PROJECT)


def _item(status: str, *, item_id: uuid.UUID | None = None, relevance: float = 0.5) -> dict:
    return {"test_case_id": str(item_id or uuid.uuid4()), "status": status, "relevance_score": relevance}


@pytest.fixture
def similar(monkeypatch):
    from app.routers import search as router

    semantic = AsyncMock()
    monkeypatch.setattr(router, "resolve_project_scope", AsyncMock(return_value=(PROJECT, None)))
    monkeypatch.setattr("app.services.semantic_search.semantic_search", semantic)

    async def call(limit: int = 5):
        return await router.find_similar_failures(
            str(SOURCE), limit=limit, db=SimpleNamespace(execute=AsyncMock(return_value=_Result())),
            current_user=MagicMock(),
        )

    return call, semantic


async def test_only_failed_and_broken_neighbours_are_kept_without_the_source(similar):
    call, semantic = similar
    failed, broken, failed_2 = _item("FAILED", relevance=0.9), _item("BROKEN", relevance=0.8), _item("FAILED", relevance=0.4)
    semantic.return_value = (
        [_item("FAILED", item_id=SOURCE, relevance=1.0), failed, _item("PASSED", relevance=0.85), broken,
         _item("SKIPPED", relevance=0.5), failed_2],
        6, 1,
    )
    result = await call(limit=5)
    assert [i["test_case_id"] for i in result["items"]] == [
        failed["test_case_id"], broken["test_case_id"], failed_2["test_case_id"],
    ]
    assert result["total"] == 3
    assert result["semantic_status"] == "ok"
    kwargs = semantic.await_args.kwargs
    assert kwargs["size"] == 20 and kwargs["raise_on_provider_error"] is True
    assert kwargs["project_id"] == str(PROJECT)


async def test_at_most_limit_and_the_candidate_window_is_capped(similar):
    call, semantic = similar
    semantic.return_value = ([_item("FAILED") for _ in range(30)], 30, 1)
    result = await call(limit=2)
    assert len(result["items"]) == 2 and result["total"] == 2
    assert semantic.await_args.kwargs["size"] == 8
    await call(limit=20)
    assert semantic.await_args.kwargs["size"] == 40


async def test_an_enum_status_is_read_by_value(similar):
    from app.models.postgres import TestStatus

    call, semantic = similar
    semantic.return_value = ([_item(TestStatus.BROKEN), _item(TestStatus.PASSED)], 2, 1)
    result = await call()
    assert len(result["items"]) == 1


async def test_nothing_similar_is_ok_with_no_items(similar):
    call, semantic = similar
    semantic.return_value = ([], 0, 0)
    result = await call()
    assert result == {"items": [], "total": 0, "query": result["query"], "semantic_status": "ok"}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ConnectError("connection refused"), "not_configured"),
        (ValueError("boom"), "unavailable"),
    ],
)
async def test_an_index_failure_is_a_status_not_an_empty_answer(similar, caplog, error, expected):
    call, semantic = similar
    semantic.side_effect = error
    caplog.set_level(logging.DEBUG)
    result = await call()
    assert result["items"] == [] and result["total"] == 0
    assert result["semantic_status"] == expected
    assert expected in SIMILAR_SEMANTIC_STATUSES
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "zq-similar-sentinel" not in logged, "the source test's name reached a log line"
    assert "zq-error-sentinel" not in logged, "the source test's error text reached a log line"
    assert str(error) not in logged, "the exception's message reached a log line (its TYPE only)"
    assert type(error).__name__ in logged


async def test_a_database_failure_inside_the_semantic_read_is_not_an_index_status(similar):
    from sqlalchemy.exc import OperationalError

    call, semantic = similar
    semantic.side_effect = OperationalError("SELECT 1", {}, Exception("db gone"))
    with pytest.raises(OperationalError):
        await call()
