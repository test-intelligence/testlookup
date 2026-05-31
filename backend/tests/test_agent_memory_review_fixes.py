"""Regression tests for the agent_memory_service review fixes (review/agent-memory-service).

Covers:
  - recall_similar skips malformed ChromaDB entry ids instead of raising
  - persist_memory_entries sanitizes free-text before persistence
  - persist_memory_entries awaits its vector-index work (no detached create_task)
  - persist_pipeline_memory returns 0 on a malformed id instead of raising
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import agent_memory_service as ams

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock()
    return db


_PID = uuid.UUID("22222222-2222-2222-2222-222222222222")
_GOOD_ID = "44444444-4444-4444-4444-444444444444"


class TestRecallSkipsMalformedIds:
    async def test_malformed_chroma_id_is_skipped_not_raised(self, mock_db):
        matches = [
            {"entry_id": _GOOD_ID, "similarity": 0.9, "distance": 0.2},
            {"entry_id": "not-a-uuid", "similarity": 0.8, "distance": 0.4},
        ]
        good_entry = MagicMock()
        good_entry.id = uuid.UUID(_GOOD_ID)
        good_entry.created_at = None
        good_entry.payload = {}
        good_entry.entity_type = "cluster"
        good_entry.entity_id = "c1"

        scalars = MagicMock()
        scalars.all.return_value = [good_entry]
        mock_db.execute.return_value = MagicMock(scalars=MagicMock(return_value=scalars))

        with patch.object(ams, "_query_similar_vectors", AsyncMock(return_value=matches)):
            results = await ams.recall_similar(mock_db, _PID, "boom", limit=10)

        assert len(results) == 1
        assert str(results[0]["memory"].id) == _GOOD_ID

    async def test_all_malformed_ids_returns_empty(self, mock_db):
        matches = [{"entry_id": "nope", "similarity": 0.9, "distance": 0.2}]
        with patch.object(ams, "_query_similar_vectors", AsyncMock(return_value=matches)):
            results = await ams.recall_similar(mock_db, _PID, "boom", limit=10)
        assert results == []
        mock_db.execute.assert_not_awaited()


class TestPersistSanitizesAndAwaitsIndexing:
    async def test_text_sanitized_before_persistence(self, mock_db):
        entries = [{
            "project_id": _PID,
            "run_id": uuid.uuid4(),
            "entity_type": "analysis",
            "entity_id": "t1",
            "error_signature": "secret@example.com failed",
            "root_cause_summary": "token=abc123 leaked",
        }]
        with patch("app.services.privacy_service.sanitize_for_persistence",
                   side_effect=lambda t: f"REDACTED({t})") as san, \
             patch.object(ams, "_index_memory_vector", AsyncMock()) as idx:
            count = await ams.persist_memory_entries(mock_db, entries)

        assert count == 1
        san.assert_any_call("secret@example.com failed")
        san.assert_any_call("token=abc123 leaked")
        added = mock_db.add.call_args_list[0].args[0]
        assert added.error_signature == "REDACTED(secret@example.com failed)"
        assert added.root_cause_summary == "REDACTED(token=abc123 leaked)"
        idx.assert_awaited_once()
        assert idx.await_args.args[1] == "REDACTED(secret@example.com failed)"

    async def test_no_signature_skips_indexing(self, mock_db):
        entries = [{
            "project_id": _PID,
            "run_id": uuid.uuid4(),
            "entity_type": "summary",
            "entity_id": "s1",
        }]
        with patch.object(ams, "_index_memory_vector", AsyncMock()) as idx:
            count = await ams.persist_memory_entries(mock_db, entries)
        assert count == 1
        idx.assert_not_awaited()


class TestPersistPipelineMemoryInvalidId:
    async def test_invalid_project_id_returns_zero(self, mock_db):
        count = await ams.persist_pipeline_memory(
            mock_db, "not-a-uuid", str(uuid.uuid4()), str(uuid.uuid4()), {},
        )
        assert count == 0
        mock_db.commit.assert_not_awaited()
