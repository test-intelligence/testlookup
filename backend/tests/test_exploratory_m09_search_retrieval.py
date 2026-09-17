"""M09 regressions for truthful fallback and project-bound evidence links."""
from __future__ import annotations

import asyncio
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


class _Scalar:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


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
    assert results[0]["entity_id"] == f"{project_id}:{suite_name}"
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


@pytest.mark.asyncio
async def test_entity_counts_serialize_queries_on_one_session(monkeypatch):
    """One request must never overlap operations on its injected AsyncSession."""
    from app.routers import search

    project_id = uuid.uuid4()

    class TrackingSession:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.calls = 0

        async def execute(self, _statement):
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            return _Scalar(1)

    db = TrackingSession()
    monkeypatch.setattr(
        search,
        "resolve_project_scope",
        AsyncMock(return_value=(project_id, {project_id})),
    )

    result = await search.get_entity_counts(
        project_id=str(project_id), db=db, current_user=MagicMock()
    )

    assert db.calls == 6
    assert db.max_active == 1
    assert set(result.values()) == {1}


@pytest.mark.asyncio
async def test_global_search_discloses_adapter_failure(monkeypatch):
    from app.services import global_search_service as service

    async def good(*_args, **_kwargs):
        return [{"entity_type": "test_case", "relevance_score": 1.0}]

    async def broken(*_args, **_kwargs):
        raise RuntimeError("defect store unavailable")

    monkeypatch.setattr(service, "_ADAPTERS", {"test_case": good, "defect": broken})
    result = await service.global_search(
        MagicMock(), q="checkout", entity_types={"test_case", "defect"}
    )

    assert result["result_status"] == "partial"
    assert result["failed_entity_types"] == ["defect"]
    assert result["counts_are_exact"] is False


@pytest.mark.asyncio
async def test_global_search_discloses_capped_adapter_sample(monkeypatch):
    from app.services import global_search_service as service

    async def capped(*_args, **_kwargs):
        return [
            {"entity_type": "release", "entity_id": str(i), "relevance_score": 1.0}
            for i in range(10)
        ]

    monkeypatch.setattr(service, "_ADAPTERS", {"release": capped})
    result = await service.global_search(MagicMock(), q="")

    assert result["result_status"] == "partial"
    assert result["failed_entity_types"] == []
    assert result["counts_are_exact"] is False


@pytest.mark.asyncio
async def test_flaky_rate_filter_is_applied_before_adapter_limit():
    from app.services.global_search_service import _search_flaky_tests

    db = SimpleNamespace(execute=AsyncMock(return_value=_Rows([])))
    await _search_flaky_tests(db, "checkout", None, None)

    sql = str(db.execute.await_args.args[0])
    having = sql.split("HAVING", 1)[1].split("LIMIT", 1)[0]
    assert having.count("FILTER") >= 2
    assert ">=" in having and "<=" in having
    assert "test_case_history.status" in having


@pytest.mark.asyncio
async def test_hybrid_fetches_enough_candidates_for_requested_page(monkeypatch):
    from app.services import search_service, semantic_search

    requested_sizes = []

    async def keywords(_db, **kwargs):
        requested_sizes.append(kwargs["size"])
        items = [
            {"test_case_id": f"keyword-{i}", "last_run_date": "", "status": "PASSED"}
            for i in range(kwargs["size"])
        ]
        return items, 50, 1

    async def semantics(_db, _q, _page, size, *_args, **_kwargs):
        items = [
            {
                "test_case_id": f"semantic-{i}",
                "last_run_date": "",
                "status": "FAILED",
                "relevance_score": 0.8,
            }
            for i in range(size)
        ]
        return items, size, 1

    monkeypatch.setattr(search_service, "search_test_cases_query", keywords)
    monkeypatch.setattr(semantic_search, "semantic_search", semantics)

    items, total, pages = await semantic_search.hybrid_search(
        MagicMock(), q="checkout", page=3, size=2
    )

    assert requested_sizes == [200]
    assert len(items) == 2
    assert total == 400
    assert pages == 200
