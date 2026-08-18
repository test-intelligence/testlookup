"""Regression: Search index health must follow the caller's project scope."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql


@pytest.mark.asyncio
async def test_index_status_resolves_active_authorized_project_scope():
    from app.routers.search import get_index_status

    project_id = uuid.uuid4()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [project_id]
    db = SimpleNamespace(execute=AsyncMock(return_value=execute_result))
    status = {"status": "healthy", "document_count": 7, "last_indexed_at": None}

    with (
        patch(
            "app.routers.search.resolve_project_scope",
            AsyncMock(return_value=(project_id, None)),
        ) as resolve,
        patch(
            "app.services.semantic_search.get_index_status",
            AsyncMock(return_value=status),
        ) as scoped_status,
    ):
        result = await get_index_status(
            project_id=str(project_id),
            db=db,
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )

    assert result == status
    resolve.assert_awaited_once()
    statement = db.execute.await_args.args[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()
    assert "projects.is_active is true" in sql
    assert str(project_id) in sql
    scoped_status.assert_awaited_once_with(
        project_id=str(project_id),
        allowed_project_ids={project_id},
    )


@pytest.mark.asyncio
async def test_index_status_scopes_non_admin_to_accessible_active_set():
    from app.routers.search import get_index_status

    accessible = {uuid.uuid4(), uuid.uuid4()}
    active = {next(iter(accessible))}
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = list(active)
    db = SimpleNamespace(execute=AsyncMock(return_value=execute_result))

    with (
        patch(
            "app.routers.search.resolve_project_scope",
            AsyncMock(return_value=(None, accessible)),
        ),
        patch(
            "app.services.semantic_search.get_index_status",
            AsyncMock(return_value={}),
        ) as scoped_status,
    ):
        await get_index_status(
            project_id=None,
            db=db,
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )

    sql = str(
        db.execute.await_args.args[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()
    assert "projects.is_active is true" in sql
    for project_id in accessible:
        assert str(project_id) in sql
    scoped_status.assert_awaited_once_with(
        project_id=None,
        allowed_project_ids=active,
    )


@pytest.mark.asyncio
async def test_chroma_index_status_counts_only_allowed_project_documents():
    from app.services import semantic_search

    allowed = {uuid.uuid4(), uuid.uuid4()}
    collection = MagicMock()
    collection.get.return_value = {"ids": ["one", "two"]}
    redis_client = MagicMock()
    redis_client.get.return_value = None

    with (
        patch.object(
            semantic_search,
            "_get_or_create_collection",
            AsyncMock(return_value=collection),
        ),
        patch("redis.Redis.from_url", return_value=redis_client),
    ):
        result = await semantic_search.get_index_status(
            allowed_project_ids=allowed,
        )

    assert result["status"] == "healthy"
    assert result["document_count"] == 2
    collection.count.assert_not_called()
    collection.get.assert_called_once()
    kwargs = collection.get.call_args.kwargs
    assert kwargs["include"] == []
    assert set(kwargs["where"]["project_id"]["$in"]) == {
        str(project_id) for project_id in allowed
    }


@pytest.mark.asyncio
async def test_chroma_index_status_empty_scope_never_reads_global_count():
    from app.services import semantic_search

    collection = MagicMock()
    with patch.object(
        semantic_search,
        "_get_or_create_collection",
        AsyncMock(return_value=collection),
    ):
        result = await semantic_search.get_index_status(allowed_project_ids=set())

    assert result["document_count"] == 0
    collection.get.assert_not_called()
    collection.count.assert_not_called()
