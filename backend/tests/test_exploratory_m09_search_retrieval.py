"""M09 regressions for truthful fallback and project-bound evidence links."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_hybrid_chroma_failure_is_labeled_keyword_fallback(monkeypatch):
    from app.routers import search as search_router
    from app.services import search_service, semantic_search

    project_id = uuid.uuid4()
    keyword_item = {
        "test_case_id": str(uuid.uuid4()),
        "source_mode_used": "keyword",
    }
    keyword = AsyncMock(return_value=([keyword_item], 1, 1))
    monkeypatch.setattr(
        search_router,
        "resolve_project_scope",
        AsyncMock(return_value=(project_id, {project_id})),
    )
    monkeypatch.setattr(search_router, "search_test_cases_query", keyword)
    monkeypatch.setattr(search_service, "search_test_cases_query", keyword)
    monkeypatch.setattr(
        semantic_search,
        "_get_or_create_collection",
        AsyncMock(side_effect=RuntimeError("chroma unavailable")),
    )

    result = await search_router.search_test_cases(
        q="checkout",
        project_id=str(project_id),
        status=None,
        days=None,
        search_type="hybrid",
        page=1,
        size=20,
        db=MagicMock(),
        current_user=MagicMock(),
    )

    assert result["search_type"] == "keyword"
    assert result["items"] == [keyword_item]
    assert keyword.await_count == 2


@pytest.mark.asyncio
async def test_suite_results_keep_project_identity_and_encode_their_link():
    from app.services.global_search_service import _search_suites

    project_id = uuid.uuid4()
    suite_name = "A&B / smoke #1"
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=_Rows(
                [
                    SimpleNamespace(
                        suite_name=suite_name,
                        test_count=3,
                        project_id=project_id,
                    )
                ]
            )
        )
    )

    results = await _search_suites(
        db,
        suite_name,
        None,
        None,
        {project_id},
    )

    statement = db.execute.await_args.args[0]
    group_by = str(statement).split("GROUP BY", 1)[1]
    assert "test_runs.project_id" in group_by
    assert results[0]["project_id"] == str(project_id)
    query = parse_qs(urlsplit(results[0]["navigation_url"]).query)
    assert query == {"name": [suite_name], "project_id": [str(project_id)]}


@pytest.mark.asyncio
async def test_similar_search_does_not_report_a_database_failure_as_no_evidence():
    from app.routers import search

    db = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("postgres unavailable")))

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        await search.find_similar_failures(
            str(uuid.uuid4()),
            limit=5,
            db=db,
            current_user=MagicMock(),
        )


@pytest.mark.asyncio
async def test_similar_search_still_treats_an_invalid_id_as_missing():
    from app.routers import search

    db = SimpleNamespace(execute=AsyncMock())

    result = await search.find_similar_failures(
        "not-a-uuid",
        limit=5,
        db=db,
        current_user=MagicMock(),
    )

    assert result == {"items": [], "total": 0, "query": "not-a-uuid"}
    db.execute.assert_not_awaited()
