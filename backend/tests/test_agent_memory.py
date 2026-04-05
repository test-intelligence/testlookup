"""
Unit tests for the Agent Memory service and router (P3 — Unified Memory & Retrieval).

All DB calls are mocked — no real database or ChromaDB required.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_memory_entry(**overrides) -> SimpleNamespace:
    """Create a fake AgentMemoryEntry-like object for testing."""
    defaults = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "pipeline_run_id": uuid.uuid4(),
        "entity_type": "cluster",
        "entity_id": "cl_001",
        "error_signature": "NullPointerException at com.app.Service.process",
        "failure_category": "PRODUCT_BUG",
        "root_cause_summary": "Null check missing in Service.process()",
        "payload": {"label": "NPE in Service", "size": 3},
        "confidence": 85,
        "resolution": None,
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_pipeline_state(**overrides) -> dict:
    """Create a mock final pipeline state."""
    base = {
        "pipeline_run_id": str(uuid.uuid4()),
        "test_run_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "build_number": "build-42",
        "pass_rate": 85.0,
        "total_tests": 100,
        "failed_test_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
        "failure_clusters": [
            {
                "cluster_id": "cl_001",
                "representative_error": "java.lang.NullPointerException",
                "regression_classification": "NEW_FAILURE",
                "label": "NPE cluster",
                "size": 3,
                "member_test_ids": ["t1", "t2", "t3"],
                "cohesion_score": 0.92,
                "confidence": 88,
            }
        ],
        "analyses": {
            "test-1": {
                "failure_category": "PRODUCT_BUG",
                "root_cause_summary": "Null check missing in handler",
                "confidence_score": 90,
                "is_flaky": False,
                "recommended_actions": ["Add null guard"],
                "evidence_references": [{"source": "stacktrace"}],
            }
        },
        "release_decision": {
            "recommendation": "NO_GO",
            "risk_score": 72,
            "blocking_issues": ["2 new regressions"],
            "conditions_for_go": ["Fix NPE cluster"],
            "confidence": 80,
        },
        "anomaly_summary": "Detected 2 new anomalies vs baseline",
        "is_regression": True,
        "anomalies": [{"type": "new_failure"}],
        "regression_tests": ["t1"],
        "executive_summary": "Build failed with 2 new regressions.",
        "fallback_used": False,
        "errors": [],
        "completed_stages": ["ingestion", "anomaly_detection", "analysis", "summary"],
    }
    base.update(overrides)
    return base


# ── Service tests ────────────────────────────────────────────────────────────


class TestPersistMemoryEntries:
    """Tests for agent_memory_service.persist_memory_entries."""

    @pytest.mark.asyncio
    async def test_persist_empty_list_returns_zero(self):
        """No entries should produce zero inserts."""
        from app.services.agent_memory_service import persist_memory_entries

        db = AsyncMock()
        count = await persist_memory_entries(db, [])
        assert count == 0
        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_persist_entries_adds_to_db(self):
        """Entries should be added to the session and committed."""
        from app.services.agent_memory_service import persist_memory_entries

        db = AsyncMock()
        entries = [
            {
                "project_id": uuid.uuid4(),
                "run_id": uuid.uuid4(),
                "entity_type": "cluster",
                "entity_id": "cl_001",
                "error_signature": "NullPointerException",
                "failure_category": "PRODUCT_BUG",
            },
            {
                "project_id": uuid.uuid4(),
                "run_id": uuid.uuid4(),
                "entity_type": "analysis",
                "entity_id": str(uuid.uuid4()),
            },
        ]

        with patch("app.services.agent_memory_service._index_memory_vector", new_callable=AsyncMock):
            count = await persist_memory_entries(db, entries)

        assert count == 2
        assert db.add.call_count == 2
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_persist_entries_without_signature_skips_vector_index(self):
        """Entries without error_signature should not trigger vector indexing."""
        from app.services.agent_memory_service import persist_memory_entries

        db = AsyncMock()
        entries = [
            {
                "project_id": uuid.uuid4(),
                "run_id": uuid.uuid4(),
                "entity_type": "summary",
                "entity_id": str(uuid.uuid4()),
                # no error_signature
            },
        ]

        with patch("app.services.agent_memory_service._index_memory_vector", new_callable=AsyncMock) as mock_idx:
            count = await persist_memory_entries(db, entries)

        assert count == 1
        mock_idx.assert_not_called()


class TestPersistPipelineMemory:
    """Tests for agent_memory_service.persist_pipeline_memory."""

    @pytest.mark.asyncio
    async def test_extracts_clusters(self):
        """Pipeline state with clusters should produce cluster memory entries."""
        from app.services.agent_memory_service import persist_pipeline_memory

        db = AsyncMock()
        state = _make_pipeline_state()

        with patch("app.services.agent_memory_service._index_memory_vector", new_callable=AsyncMock):
            count = await persist_pipeline_memory(
                db,
                state["project_id"],
                state["test_run_id"],
                state["pipeline_run_id"],
                state,
            )

        # 1 cluster + 1 analysis + 1 release_decision + 1 anomaly + 1 summary = 5
        assert count == 5
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_state_returns_zero(self):
        """Pipeline state with no relevant data should produce no entries."""
        from app.services.agent_memory_service import persist_pipeline_memory

        db = AsyncMock()
        state = {
            "failure_clusters": [],
            "analyses": {},
            "release_decision": None,
            "anomaly_summary": None,
            "executive_summary": None,
        }
        pid = str(uuid.uuid4())
        rid = str(uuid.uuid4())

        count = await persist_pipeline_memory(db, pid, rid, str(uuid.uuid4()), state)
        assert count == 0

    @pytest.mark.asyncio
    async def test_extracts_release_decision(self):
        """Pipeline state with release_decision should produce a release_decision entry."""
        from app.services.agent_memory_service import persist_pipeline_memory

        db = AsyncMock()
        state = _make_pipeline_state(
            failure_clusters=[],
            analyses={},
            anomaly_summary=None,
            executive_summary=None,
        )

        with patch("app.services.agent_memory_service._index_memory_vector", new_callable=AsyncMock):
            count = await persist_pipeline_memory(
                db,
                state["project_id"],
                state["test_run_id"],
                state["pipeline_run_id"],
                state,
            )

        assert count == 1  # only release_decision
        added_obj = db.add.call_args[0][0]
        assert added_obj.entity_type == "release_decision"


class TestListProjectMemories:
    """Tests for agent_memory_service.list_project_memories."""

    @pytest.mark.asyncio
    async def test_returns_entries_and_count(self):
        """Should return paginated entries and a total count."""
        from app.services.agent_memory_service import list_project_memories

        entry = _make_memory_entry()
        db = AsyncMock()
        # count query
        db.execute = AsyncMock(side_effect=[
            MagicMock(scalar=MagicMock(return_value=1)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[entry])))),
        ])

        entries, total = await list_project_memories(db, entry.project_id)
        assert total == 1
        assert len(entries) == 1
        assert entries[0].entity_type == "cluster"


class TestGetRunMemoryTimeline:
    """Tests for agent_memory_service.get_run_memory_timeline."""

    @pytest.mark.asyncio
    async def test_groups_by_entity_type(self):
        """Entries should be grouped by entity_type."""
        from app.services.agent_memory_service import get_run_memory_timeline

        run_id = uuid.uuid4()
        entries = [
            _make_memory_entry(run_id=run_id, entity_type="cluster"),
            _make_memory_entry(run_id=run_id, entity_type="cluster"),
            _make_memory_entry(run_id=run_id, entity_type="analysis"),
        ]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=entries)))
        ))

        grouped, total = await get_run_memory_timeline(db, run_id)
        assert total == 3
        assert len(grouped["cluster"]) == 2
        assert len(grouped["analysis"]) == 1

    @pytest.mark.asyncio
    async def test_empty_run_returns_empty(self):
        """Run with no entries should return empty dict."""
        from app.services.agent_memory_service import get_run_memory_timeline

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        ))

        grouped, total = await get_run_memory_timeline(db, uuid.uuid4())
        assert total == 0
        assert grouped == {}


class TestRecallSimilar:
    """Tests for agent_memory_service.recall_similar."""

    @pytest.mark.asyncio
    async def test_returns_matched_entries(self):
        """Similar vector matches should be resolved to DB entries."""
        from app.services.agent_memory_service import recall_similar

        entry = _make_memory_entry()

        with patch("app.services.agent_memory_service._query_similar_vectors", new_callable=AsyncMock) as mock_vec:
            mock_vec.return_value = [{"entry_id": str(entry.id), "similarity": 0.92}]

            db = AsyncMock()
            db.execute = AsyncMock(return_value=MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[entry])))
            ))

            results = await recall_similar(
                db, entry.project_id, "NullPointerException", limit=5,
            )

        assert len(results) == 1
        assert results[0]["similarity"] == 0.92
        assert results[0]["memory"].entity_type == "cluster"

    @pytest.mark.asyncio
    async def test_no_matches_returns_empty(self):
        """No vector matches should produce an empty list."""
        from app.services.agent_memory_service import recall_similar

        with patch("app.services.agent_memory_service._query_similar_vectors", new_callable=AsyncMock) as mock_vec:
            mock_vec.return_value = []

            db = AsyncMock()
            results = await recall_similar(db, uuid.uuid4(), "some error", limit=5)

        assert results == []


class TestBuildAnalysisSignature:
    """Tests for _build_analysis_signature helper."""

    def test_builds_from_category_and_summary(self):
        from app.services.agent_memory_service import _build_analysis_signature

        sig = _build_analysis_signature({
            "failure_category": "INFRASTRUCTURE",
            "root_cause_summary": "DB pool exhausted",
        })
        assert "INFRASTRUCTURE" in sig
        assert "DB pool exhausted" in sig

    def test_empty_analysis_returns_empty_string(self):
        from app.services.agent_memory_service import _build_analysis_signature

        sig = _build_analysis_signature({})
        assert sig == ""

    def test_truncates_long_content(self):
        from app.services.agent_memory_service import _build_analysis_signature

        sig = _build_analysis_signature({
            "root_cause_summary": "x" * 2000,
        })
        assert len(sig) <= 510  # 500 truncation + separator overhead


# ── Workflow integration tests ───────────────────────────────────────────────


class TestWorkflowMemoryPersistence:
    """Tests for persist_pipeline_memory integration (workflow calls this after pipeline)."""

    @pytest.mark.asyncio
    async def test_persist_pipeline_memory_called_with_state(self):
        """persist_pipeline_memory should accept a full pipeline state and persist entries."""
        from app.services.agent_memory_service import persist_pipeline_memory

        db = AsyncMock()
        state = _make_pipeline_state()

        with patch("app.services.agent_memory_service._index_memory_vector", new_callable=AsyncMock):
            count = await persist_pipeline_memory(
                db,
                state["project_id"],
                state["test_run_id"],
                state["pipeline_run_id"],
                state,
            )

        assert count >= 1
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_persist_pipeline_memory_handles_empty_state(self):
        """Empty pipeline state should not raise and should return 0."""
        from app.services.agent_memory_service import persist_pipeline_memory

        db = AsyncMock()
        count = await persist_pipeline_memory(
            db,
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            {"failure_clusters": [], "analyses": {}, "release_decision": None,
             "anomaly_summary": None, "executive_summary": None},
        )

        assert count == 0
        db.commit.assert_not_called()


# ── Schema validation tests ─────────────────────────────────────────────────


class TestSchemaValidation:
    """Tests for Pydantic schema serialization."""

    def test_memory_entry_response_from_attributes(self):
        from app.models.schemas import AgentMemoryEntryResponse

        entry = _make_memory_entry()
        response = AgentMemoryEntryResponse.model_validate(entry)
        assert response.entity_type == "cluster"
        assert response.entity_id == "cl_001"
        assert response.confidence == 85

    def test_similar_memory_response_bounds(self):
        from app.models.schemas import SimilarMemoryResponse, AgentMemoryEntryResponse

        entry = _make_memory_entry()
        mem_resp = AgentMemoryEntryResponse.model_validate(entry)

        resp = SimilarMemoryResponse(memory=mem_resp, similarity=0.92)
        assert resp.similarity == 0.92

    def test_recall_request_validates_min_signature(self):
        from app.models.schemas import SimilarMemoryRecallRequest

        with pytest.raises(Exception):
            SimilarMemoryRecallRequest(error_signature="ab")  # too short

        req = SimilarMemoryRecallRequest(error_signature="NullPointerException at line 42")
        assert req.limit == 10  # default

    def test_memory_list_response(self):
        from app.models.schemas import AgentMemoryListResponse, AgentMemoryEntryResponse

        entry = _make_memory_entry()
        resp = AgentMemoryListResponse(
            total=1,
            items=[AgentMemoryEntryResponse.model_validate(entry)],
            page=1,
            size=50,
        )
        assert resp.total == 1
        assert len(resp.items) == 1

    def test_memory_timeline_response(self):
        from app.models.schemas import MemoryTimelineResponse

        run_id = uuid.uuid4()
        project_id = uuid.uuid4()
        resp = MemoryTimelineResponse(
            run_id=run_id,
            project_id=project_id,
            entries_by_type={"cluster": [], "analysis": []},
            total_entries=0,
        )
        assert resp.total_entries == 0
        assert "cluster" in resp.entries_by_type


# ── Security tests ───────────────────────────────────────────────────────────


class TestSecuritySanitization:
    """Tests to ensure memory entries don't propagate unsafe content."""

    @pytest.mark.asyncio
    async def test_error_signature_truncated(self):
        """Very long error signatures should be truncated to prevent abuse."""
        from app.services.agent_memory_service import persist_pipeline_memory

        db = AsyncMock()
        state = _make_pipeline_state()
        # Inject an excessively long error message
        state["failure_clusters"][0]["representative_error"] = "X" * 100000

        with patch("app.services.agent_memory_service._index_memory_vector", new_callable=AsyncMock):
            count = await persist_pipeline_memory(
                db,
                state["project_id"],
                state["test_run_id"],
                state["pipeline_run_id"],
                state,
            )

        assert count >= 1
        # Check the cluster entry's error_signature was truncated
        added_calls = [call[0][0] for call in db.add.call_args_list]
        cluster_entries = [e for e in added_calls if hasattr(e, 'entity_type') and e.entity_type == "cluster"]
        assert len(cluster_entries) >= 1
        assert len(cluster_entries[0].error_signature) <= 5000

    @pytest.mark.asyncio
    async def test_payload_does_not_include_raw_secrets(self):
        """Pipeline state should not leak sensitive data into memory payloads."""
        from app.services.agent_memory_service import persist_pipeline_memory

        db = AsyncMock()
        state = _make_pipeline_state()
        # The service only extracts specific fields — arbitrary keys in state
        # should not leak into memory entries
        state["_internal_secret"] = "supersecret123"

        with patch("app.services.agent_memory_service._index_memory_vector", new_callable=AsyncMock):
            await persist_pipeline_memory(
                db,
                state["project_id"],
                state["test_run_id"],
                state["pipeline_run_id"],
                state,
            )

        added_calls = [call[0][0] for call in db.add.call_args_list]
        for entry in added_calls:
            if hasattr(entry, 'payload') and entry.payload:
                payload_str = str(entry.payload)
                assert "supersecret123" not in payload_str
