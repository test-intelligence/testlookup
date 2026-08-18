"""Regression guards for tenant-safe similar-failure search."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


@pytest.mark.asyncio
async def test_similar_failures_authorizes_and_scopes_to_source_project(monkeypatch):
    from app.routers import search

    source_id = uuid.uuid4()
    project_id = uuid.uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=_Result(
                SimpleNamespace(
                    test_name="test_login",
                    error_message="timeout",
                    project_id=project_id,
                )
            )
        )
    )
    resolve_scope = AsyncMock(return_value=(project_id, {project_id}))
    semantic = AsyncMock(
        return_value=([{"test_case_id": str(uuid.uuid4())}], 1, 1)
    )
    monkeypatch.setattr(search, "resolve_project_scope", resolve_scope)
    monkeypatch.setattr("app.services.semantic_search.semantic_search", semantic)

    result = await search.find_similar_failures(
        str(source_id),
        limit=5,
        db=db,
        current_user=MagicMock(),
    )

    source_sql = str(db.execute.await_args.args[0]).lower()
    assert "join test_runs" in source_sql
    resolve_scope.assert_awaited_once()
    assert resolve_scope.await_args.args[2] == str(project_id)
    assert semantic.await_args.kwargs["project_id"] == str(project_id)
    assert semantic.await_args.kwargs["allowed_project_ids"] == {project_id}
    assert result["total"] == 1


@pytest.mark.asyncio
async def test_similar_failures_stops_before_search_when_project_is_forbidden(
    monkeypatch,
):
    from app.routers import search

    project_id = uuid.uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=_Result(
                SimpleNamespace(
                    test_name="test_login",
                    error_message="timeout",
                    project_id=project_id,
                )
            )
        )
    )
    resolve_scope = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Project access denied")
    )
    semantic = AsyncMock(return_value=([], 0, 0))
    monkeypatch.setattr(search, "resolve_project_scope", resolve_scope)
    monkeypatch.setattr("app.services.semantic_search.semantic_search", semantic)

    with pytest.raises(HTTPException) as raised:
        await search.find_similar_failures(
            str(uuid.uuid4()),
            limit=5,
            db=db,
            current_user=MagicMock(),
        )

    assert raised.value.status_code == 403
    semantic.assert_not_awaited()
