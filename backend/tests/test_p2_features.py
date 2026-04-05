"""
Unit tests for P2 features:
  - Semantic similarity cache (ChromaDB)
  - Agent reasoning OTEL tracing
  - Intermediate artifact persistence (MinIO)
  - Immutable pipeline event log (MongoDB)
"""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Semantic Cache Tests ─────────────────────────────────────────────────────


class TestSemanticCache:
    """Verify ChromaDB-backed semantic similarity cache."""

    def test_build_signature_combines_fields(self):
        from app.services.semantic_cache import _build_signature

        sig = _build_signature("TestLogin", "Expected 200 got 401", "at Login.java:45")
        assert "TestLogin" in sig
        assert "Expected 200" in sig
        assert "Login.java" in sig

    def test_build_signature_handles_empty_fields(self):
        from app.services.semantic_cache import _build_signature

        sig = _build_signature("TestLogin", "", "")
        assert sig == "TestLogin"

    def test_build_signature_truncates_long_inputs(self):
        from app.services.semantic_cache import _build_signature

        long_error = "x" * 2000
        sig = _build_signature("Test", long_error, "")
        # Should be capped to avoid excessive embedding costs
        assert len(sig) < 2000

    @pytest.mark.asyncio
    async def test_semantic_lookup_returns_none_on_no_inputs(self):
        from app.services.semantic_cache import semantic_cache_lookup

        result = await semantic_cache_lookup("TestLogin", "", "")
        assert result is None

    @pytest.mark.asyncio
    async def test_semantic_lookup_returns_none_on_chroma_error(self):
        from app.services.semantic_cache import semantic_cache_lookup

        with patch("app.services.semantic_cache._get_or_create_collection", side_effect=Exception("unavailable")):
            result = await semantic_cache_lookup("TestLogin", "error", "trace")
            assert result is None

    @pytest.mark.asyncio
    async def test_semantic_lookup_returns_none_below_threshold(self):
        from app.services.semantic_cache import semantic_cache_lookup

        mock_collection = MagicMock()
        # Distance 1.8 → similarity = 1 - (1.8/2) = 0.1, well below 0.85 threshold
        mock_collection.query.return_value = {
            "ids": [["doc-1"]],
            "distances": [[1.8]],
            "metadatas": [[{"analysis_json": '{"failure_category": "BUG"}'}]],
        }
        with patch("app.services.semantic_cache._get_or_create_collection", new_callable=AsyncMock, return_value=mock_collection):
            with patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
                result = await semantic_cache_lookup("TestLogin", "error", "trace")
                assert result is None

    @pytest.mark.asyncio
    async def test_semantic_lookup_returns_cached_above_threshold(self):
        from app.services.semantic_cache import semantic_cache_lookup

        mock_collection = MagicMock()
        cached = {"failure_category": "INFRASTRUCTURE", "confidence_score": 90}
        # Distance 0.1 → similarity = 1 - (0.1/2) = 0.95, above 0.85 threshold
        mock_collection.query.return_value = {
            "ids": [["doc-1"]],
            "distances": [[0.1]],
            "metadatas": [[{"analysis_json": json.dumps(cached)}]],
        }
        with patch("app.services.semantic_cache._get_or_create_collection", new_callable=AsyncMock, return_value=mock_collection):
            with patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
                result = await semantic_cache_lookup("TestLogin", "error", "trace")
                assert result is not None
                assert result["failure_category"] == "INFRASTRUCTURE"
                assert result["semantic_cache_hit"] is True
                assert result["semantic_similarity"] >= 0.85

    @pytest.mark.asyncio
    async def test_semantic_store_upserts_to_collection(self):
        from app.services.semantic_cache import semantic_cache_store

        mock_collection = MagicMock()
        with patch("app.services.semantic_cache._get_or_create_collection", new_callable=AsyncMock, return_value=mock_collection):
            with patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
                await semantic_cache_store(
                    "TestLogin", "error msg", "trace",
                    {"failure_category": "BUG", "confidence_score": 80},
                )
                mock_collection.upsert.assert_called_once()
                call_args = mock_collection.upsert.call_args
                assert len(call_args.kwargs["ids"]) == 1
                assert "analysis_json" in call_args.kwargs["metadatas"][0]

    @pytest.mark.asyncio
    async def test_semantic_cache_stats_returns_status(self):
        from app.services.semantic_cache import get_semantic_cache_stats

        mock_collection = MagicMock()
        mock_collection.count.return_value = 42
        with patch("app.services.semantic_cache._get_or_create_collection", new_callable=AsyncMock, return_value=mock_collection):
            with patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
                stats = await get_semantic_cache_stats()
                assert stats["status"] == "healthy"
                assert stats["document_count"] == 42


# ── Agent OTEL Tracing Tests ────────────────────────────────────────────────


class TestAgentTracing:
    """Verify OTEL span recording for agent tool calls."""

    def test_record_tool_spans_adds_events(self):
        from app.services.agent import _record_tool_spans

        mock_span = MagicMock()
        # Simulate intermediate_steps: list of (AgentAction, observation)
        mock_action = MagicMock()
        mock_action.tool = "fetch_allure_stacktrace"
        mock_action.tool_input = "test-case-123"
        steps = [(mock_action, "Stack trace output here")]

        _record_tool_spans(mock_span, steps)

        mock_span.add_event.assert_called_once()
        call_args = mock_span.add_event.call_args
        assert call_args[0][0] == "tool_call.fetch_allure_stacktrace"
        assert call_args[1]["attributes"]["tool.name"] == "fetch_allure_stacktrace"

    def test_record_tool_spans_handles_multiple_tools(self):
        from app.services.agent import _record_tool_spans

        mock_span = MagicMock()
        steps = []
        for tool_name in ["fetch_allure_stacktrace", "query_splunk_logs", "check_test_flakiness"]:
            action = MagicMock()
            action.tool = tool_name
            action.tool_input = "input"
            steps.append((action, f"output for {tool_name}"))

        _record_tool_spans(mock_span, steps)
        assert mock_span.add_event.call_count == 3

    def test_record_tool_spans_handles_empty_steps(self):
        from app.services.agent import _record_tool_spans

        mock_span = MagicMock()
        _record_tool_spans(mock_span, [])
        mock_span.add_event.assert_not_called()

    def test_record_tool_spans_handles_malformed_steps(self):
        from app.services.agent import _record_tool_spans

        mock_span = MagicMock()
        # Malformed step — no tool attribute
        _record_tool_spans(mock_span, [("not-an-action",)])
        # Should not crash, may or may not add event depending on structure
        # The important thing is no exception raised


# ── Artifact Store Tests ─────────────────────────────────────────────────────


class TestArtifactStore:
    """Verify intermediate artifact persistence to object storage."""

    def test_artifact_key_format(self):
        from app.services.artifact_store import _artifact_key

        key = _artifact_key("run-123", "analysis", "tc-456")
        assert "run-123" in key
        assert "analysis" in key
        assert "tc-456" in key
        assert key.endswith(".json")
        assert key.startswith("pipeline/")

    @pytest.mark.asyncio
    async def test_store_artifact_calls_put_object(self):
        from app.services.artifact_store import store_artifact

        mock_storage = AsyncMock()
        mock_storage.put_object = AsyncMock()
        with patch("app.db.storage.get_storage_provider", return_value=mock_storage):
            uri = await store_artifact("run-1", "analysis", "tc-1", {"key": "value"})
            assert uri  # non-empty URI
            mock_storage.put_object.assert_called_once()
            call_kwargs = mock_storage.put_object.call_args.kwargs
            assert call_kwargs["content_type"] == "application/json"
            assert call_kwargs["bucket"] == "pipeline-artifacts"

    @pytest.mark.asyncio
    async def test_store_artifact_returns_empty_on_failure(self):
        from app.services.artifact_store import store_artifact

        mock_storage = AsyncMock()
        mock_storage.put_object = AsyncMock(side_effect=Exception("storage down"))
        with patch("app.db.storage.get_storage_provider", return_value=mock_storage):
            uri = await store_artifact("run-1", "analysis", "tc-1", {"key": "value"})
            assert uri == ""

    @pytest.mark.asyncio
    async def test_load_artifact_returns_data(self):
        from app.services.artifact_store import load_artifact

        mock_storage = AsyncMock()
        mock_storage.get_object_content = AsyncMock(return_value=b'{"key": "value"}')
        with patch("app.db.storage.get_storage_provider", return_value=mock_storage):
            data = await load_artifact("pipeline-artifacts/pipeline/2026/04/02/run-1/analysis/tc-1.json")
            assert data == {"key": "value"}

    @pytest.mark.asyncio
    async def test_load_artifact_returns_none_on_empty_uri(self):
        from app.services.artifact_store import load_artifact

        result = await load_artifact("")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_artifact_returns_none_on_error(self):
        from app.services.artifact_store import load_artifact

        mock_storage = AsyncMock()
        mock_storage.get_object_content = AsyncMock(side_effect=Exception("not found"))
        with patch("app.db.storage.get_storage_provider", return_value=mock_storage):
            result = await load_artifact("pipeline-artifacts/some/key.json")
            assert result is None


# ── Pipeline Event Log Tests ─────────────────────────────────────────────────


class TestPipelineEventLog:
    """Verify immutable event log operations."""

    @pytest.mark.asyncio
    async def test_emit_event_inserts_to_mongo(self):
        from app.services.pipeline_event_log import emit_event

        mock_collection = AsyncMock()
        mock_collection.insert_one = AsyncMock()
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)

        with patch("app.services.pipeline_event_log.get_mongo_db", return_value=mock_db):
            await emit_event(
                "run-123", "stage_started",
                stage_name="ingestion",
                detail={"message": "starting"},
            )
            mock_collection.insert_one.assert_called_once()
            event = mock_collection.insert_one.call_args[0][0]
            assert event["pipeline_run_id"] == "run-123"
            assert event["event_type"] == "stage_started"
            assert event["stage_name"] == "ingestion"
            assert "timestamp" in event

    @pytest.mark.asyncio
    async def test_emit_event_does_not_raise_on_error(self):
        from app.services.pipeline_event_log import emit_event

        with patch("app.services.pipeline_event_log.get_mongo_db", side_effect=Exception("mongo down")):
            # Should not raise — event logging is best-effort
            await emit_event("run-123", "stage_started")

    @pytest.mark.asyncio
    async def test_get_pipeline_timeline_returns_events(self):
        from app.services.pipeline_event_log import get_pipeline_timeline

        mock_events = [
            {"event_type": "stage_started", "stage_name": "ingestion", "timestamp": datetime.now(timezone.utc)},
            {"event_type": "stage_completed", "stage_name": "ingestion", "timestamp": datetime.now(timezone.utc)},
        ]
        mock_cursor = AsyncMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=mock_events)

        mock_collection = MagicMock()
        mock_collection.find = MagicMock(return_value=mock_cursor)
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)

        with patch("app.services.pipeline_event_log.get_mongo_db", return_value=mock_db):
            events = await get_pipeline_timeline("run-123")
            assert len(events) == 2
            assert events[0]["event_type"] == "stage_started"

    @pytest.mark.asyncio
    async def test_get_pipeline_timeline_returns_empty_on_error(self):
        from app.services.pipeline_event_log import get_pipeline_timeline

        with patch("app.services.pipeline_event_log.get_mongo_db", side_effect=Exception("mongo down")):
            events = await get_pipeline_timeline("run-123")
            assert events == []

    @pytest.mark.asyncio
    async def test_emit_event_includes_test_case_id(self):
        from app.services.pipeline_event_log import emit_event

        mock_collection = AsyncMock()
        mock_collection.insert_one = AsyncMock()
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)

        with patch("app.services.pipeline_event_log.get_mongo_db", return_value=mock_db):
            await emit_event(
                "run-123", "cache_hit",
                test_case_id="tc-456",
                detail={"type": "redis_exact"},
            )
            event = mock_collection.insert_one.call_args[0][0]
            assert event["test_case_id"] == "tc-456"
            assert event["detail"]["type"] == "redis_exact"
