"""W12 SRCH-01: ``GET /search`` says WHY a semantic / hybrid search answered with keyword rows.

``search_type`` already said WHICH path answered; it could not say why. An
outage and an honest "no semantic match" both read ``keyword``, so the page
could not tell a person their index is down. Now ``/search`` passes
``raise_on_provider_error=True`` and records the reason:

* ``semantic_unavailable`` -- the primary raised (the index is down);
* ``no_semantic_match`` -- the primary answered nothing;
* ``no_text`` -- the query is filters only: nothing to embed, keyword answers.

Driven through the REAL handler (``routers.search.search_test_cases``) with
its collaborators patched where they are looked up.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from prometheus_client import REGISTRY

from app.core.search_vocab import FALLBACK_TEXT, QUERY_TOO_LONG

PROJECT = uuid.uuid4()
KEYWORD_ROW = {"test_case_id": str(uuid.uuid4()), "source_mode_used": "keyword"}
SEMANTIC_ROW = {"test_case_id": str(uuid.uuid4()), "source_mode_used": "semantic"}


def _counter(search_type: str, status: str) -> float:
    return REGISTRY.get_sample_value(
        "testlookup_semantic_search_total", {"search_type": search_type, "status": status},
    ) or 0.0


@pytest.fixture
def wired(monkeypatch):
    from app.routers import search as router
    from app.services import semantic_search

    keyword = AsyncMock(return_value=([KEYWORD_ROW], 1, 1))
    semantic = AsyncMock(return_value=([SEMANTIC_ROW], 1, 1))
    hybrid = AsyncMock(return_value=([SEMANTIC_ROW], 1, 1))
    monkeypatch.setattr(router, "resolve_project_scope", AsyncMock(return_value=(PROJECT, None)))
    monkeypatch.setattr(router, "search_test_cases_query", keyword)
    monkeypatch.setattr(semantic_search, "semantic_search", semantic)
    monkeypatch.setattr(semantic_search, "hybrid_search", hybrid)
    return router, keyword, semantic, hybrid


async def _search(router, q: str, search_type: str, **extra):
    return await router.search_test_cases(
        q=q, project_id=str(PROJECT), status=None, days=None, search_type=search_type,
        page=1, size=20, db=MagicMock(), current_user=MagicMock(), **extra,
    )


async def test_an_outage_is_semantic_unavailable_with_the_keyword_rows(wired):
    router, keyword, semantic, _ = wired
    semantic.side_effect = RuntimeError("chroma down")
    before = _counter("semantic", "fallback")

    result = await _search(router, "checkout", "semantic")

    assert result["search_type"] == "keyword"
    assert result["requested_search_type"] == "semantic"
    assert result["fallback_reason"] == "semantic_unavailable"
    assert result["fallback_message"] == FALLBACK_TEXT["semantic_unavailable"]
    assert result["items"] == [KEYWORD_ROW], "an outage must never be an empty page"
    assert semantic.await_args.kwargs["raise_on_provider_error"] is True
    assert semantic.await_args.kwargs["q"] == "checkout"
    assert _counter("semantic", "fallback") == before + 1
    assert result["counts_are_exact"] is True and result["result_status"] == "complete"


@pytest.mark.parametrize("search_type", ["semantic", "hybrid"])
async def test_the_fallback_log_line_carries_the_error_type_not_its_text(wired, caplog, search_type):
    """A database error's text holds the bound search pattern; the fallback logs its TYPE only."""
    import logging

    router, _, semantic, hybrid = wired
    leak = RuntimeError("[SQL: SELECT …] [parameters: ('%zq-fallback-sentinel%',)]")
    (semantic if search_type == "semantic" else hybrid).side_effect = leak
    caplog.set_level(logging.DEBUG)
    result = await _search(router, "zq-fallback-sentinel", search_type)
    assert result["fallback_reason"] == "semantic_unavailable"
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "zq-fallback-sentinel" not in logged
    assert "RuntimeError" in logged


async def test_an_empty_semantic_answer_is_no_semantic_match(wired):
    router, _, semantic, _ = wired
    semantic.return_value = ([], 0, 0)
    result = await _search(router, "checkout", "semantic")
    assert result["fallback_reason"] == "no_semantic_match"
    assert result["fallback_message"] == "No semantic matches — showing keyword results."
    assert result["items"] == [KEYWORD_ROW]


async def test_hybrid_raising_is_semantic_unavailable(wired):
    router, _, _, hybrid = wired
    hybrid.side_effect = ConnectionError("no chroma")
    result = await _search(router, "checkout", "hybrid")
    assert result["search_type"] == "keyword"
    assert result["requested_search_type"] == "hybrid"
    assert result["fallback_reason"] == "semantic_unavailable"


async def test_hybrid_empty_is_no_semantic_match(wired):
    router, _, _, hybrid = wired
    hybrid.return_value = ([], 0, 0)
    result = await _search(router, "checkout", "hybrid")
    assert result["fallback_reason"] == "no_semantic_match"


async def test_a_semantic_answer_has_no_fallback_and_is_a_lower_bound(wired):
    router, keyword, _, _ = wired
    result = await _search(router, "checkout", "semantic")
    assert result["search_type"] == "semantic"
    assert result["fallback_reason"] is None and result["fallback_message"] is None
    assert result["items"] == [SEMANTIC_ROW]
    assert result["result_status"] == "partial" and result["counts_are_exact"] is False
    keyword.assert_not_awaited()


async def test_filters_only_is_no_text_and_never_calls_the_semantic_index(wired):
    router, keyword, semantic, _ = wired
    before = _counter("semantic", "fallback")
    result = await _search(router, "status:failed", "semantic")
    semantic.assert_not_awaited()
    assert keyword.await_args.kwargs["q"] == ""
    assert keyword.await_args.kwargs["status"] == "FAILED"
    assert result["fallback_reason"] == "no_text"
    assert result["fallback_message"] == FALLBACK_TEXT["no_text"]
    assert result["search_type"] == "keyword"
    assert _counter("semantic", "fallback") == before + 1


async def test_a_filter_this_path_cannot_apply_searches_nothing_and_says_so(wired):
    router, keyword, semantic, hybrid = wired
    result = await _search(router, "checkout branch:main", "semantic")
    keyword.assert_not_awaited()
    semantic.assert_not_awaited()
    hybrid.assert_not_awaited()
    assert result["items"] == [] and result["total"] == 0
    assert result["parsed"]["unsupported_by"] == {"test_case": ["branch"]}
    assert result["parsed"]["searched_entity_types"] == []
    assert result["parsed"]["notes"] == ["branch: works in Keyword mode only, so no semantic search was run."]
    assert result["fallback_reason"] is None
    assert result["scope"]["release"] == "not_applicable"


async def test_two_statuses_are_blocked_on_this_path(wired):
    router, keyword, semantic, _ = wired
    result = await _search(router, "status:failed status:error", "semantic")
    semantic.assert_not_awaited()
    keyword.assert_not_awaited()
    assert result["parsed"]["unsupported_by"] == {"test_case": ["status"]}


async def test_type_naming_another_kind_is_blocked(wired):
    router, keyword, _, _ = wired
    result = await _search(router, "type:run checkout", "keyword")
    keyword.assert_not_awaited()
    assert result["parsed"]["unsupported_by"] == {"test_case": ["type"]}


async def test_since_and_one_status_are_applied(wired):
    router, keyword, semantic, _ = wired
    await _search(router, "since:24h status:error checkout", "semantic")
    kwargs = semantic.await_args.kwargs
    assert kwargs["q"] == "checkout"
    assert kwargs["status"] == "BROKEN"
    assert kwargs["days"] == 1


async def test_keyword_has_no_fallback_reason(wired):
    router, keyword, _, _ = wired
    result = await _search(router, "checkout", "keyword")
    assert result["fallback_reason"] is None
    assert result["requested_search_type"] == result["search_type"] == "keyword"
    assert result["parsed"]["text"] == "checkout"
    assert result["parsed"]["searched_entity_types"] == ["test_case"]
    assert keyword.await_args.kwargs["q"] == "checkout"


async def test_a_501_character_query_is_refused(wired):
    router, keyword, _, _ = wired
    with pytest.raises(HTTPException) as raised:
        await _search(router, "x" * 501, "keyword")
    assert raised.value.status_code == 422
    assert raised.value.detail == QUERY_TOO_LONG == "Search text is limited to 500 characters."
    keyword.assert_not_awaited()
    # 500 characters (after stripping) is allowed.
    await _search(router, "  " + "x" * 500 + "  ", "keyword")


async def test_the_global_route_refuses_a_501_character_query(wired):
    router, *_ = wired
    with pytest.raises(HTTPException) as raised:
        await router.global_search_endpoint(
            q="y" * 501, project_id=None, entity_types=None, days=None, page=1, size=20,
            db=MagicMock(), current_user=MagicMock(),
        )
    assert raised.value.status_code == 422 and raised.value.detail == QUERY_TOO_LONG
