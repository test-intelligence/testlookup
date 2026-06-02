"""Guard for knowledge_chunking_service tenant-metadata foundation.

Reviewed in review/knowledge-chunking-service (2026-06-02): clean. The
ChromaDB collection ``knowledge_chunks`` is GLOBAL (not per-project), which is
tenant-safe only because every chunk carries ``project_id`` in its metadata and
``rag_retrieval_service.retrieve_chunks`` applies a mandatory
``{"project_id": {"$eq": ...}}`` filter on every query (same model as
semantic_search). This test pins that foundation: ``chunk_and_index`` must stamp
``project_id`` on BOTH the ChromaDB metadata and the PG ``KnowledgeChunk`` row —
if a refactor dropped it, the retrieval filter would silently break tenant
isolation. It also pins the stage-only contract (no commit on the injected
worker session).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.services import knowledge_chunking_service as svc  # noqa: E402


class _Result:
    rowcount = 0


class _FakeDB:
    def __init__(self):
        self.added: list = []
        self.committed = False

    async def execute(self, *a, **k):
        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_chunk_and_index_stamps_project_id_on_chroma_and_pg():
    project_id = uuid.uuid4()
    source = SimpleNamespace(
        id=uuid.uuid4(), project_id=project_id, source_type="external_url",
    )
    content = SimpleNamespace(
        raw_text=(
            "## Requirements\n"
            "REQ-1 The system shall authenticate the user before granting access.\n"
            "AC-1 Given a valid token, when the user calls the API, then 200 is returned.\n"
        ),
        content_hash="h1",
    )

    collection = MagicMock()
    collection.upsert = MagicMock()
    db = _FakeDB()

    with patch.object(svc, "_get_or_create_knowledge_collection",
                      AsyncMock(return_value=collection)):
        count = await svc.chunk_and_index(db, source, content, sync_version=3)

    assert count >= 1
    # Stage-only: the worker caller (run_sync) owns the commit.
    assert db.committed is False

    # Every ChromaDB metadata row carries this project's id.
    upsert_metas = []
    for call in collection.upsert.call_args_list:
        upsert_metas.extend(call.kwargs["metadatas"])
    assert upsert_metas, "no chunks upserted to ChromaDB"
    assert all(m["project_id"] == str(project_id) for m in upsert_metas)
    assert all(m["is_active"] == 1 for m in upsert_metas)

    # Every persisted PG chunk row carries this project's id.
    chunk_rows = [r for r in db.added if r.__class__.__name__ == "KnowledgeChunk"]
    assert len(chunk_rows) == count
    assert all(r.project_id == project_id for r in chunk_rows)


@pytest.mark.asyncio
async def test_chunk_and_index_returns_zero_for_empty_content():
    source = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), source_type="x")
    content = SimpleNamespace(raw_text="   ", content_hash="h")
    db = _FakeDB()
    with patch.object(svc, "_get_or_create_knowledge_collection", AsyncMock()):
        count = await svc.chunk_and_index(db, source, content, sync_version=1)
    assert count == 0
    assert db.added == []
