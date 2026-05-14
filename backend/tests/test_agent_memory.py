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

        # 1 cluster + 1 analysis + 1 release_decision + 1 anomaly + 1 summary
        # + 1 analysis evidence memory.
        assert count == 6
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

    @pytest.mark.asyncio
    async def test_extracts_evidence_defects_release_snapshot_and_ownership(self):
        """R3.3 missing entity memories should be persisted from pipeline state."""
        from app.services.agent_memory_service import persist_pipeline_memory

        db = AsyncMock()
        state = _make_pipeline_state(
            release_decision={
                "recommendation": "NO_GO",
                "risk_score": 72,
                "blocking_issues": ["2 new regressions"],
                "conditions_for_go": ["Fix NPE cluster"],
                "confidence": 80,
                "score_model_version": 3,
                "input_snapshot": {
                    "score_model_version": 3,
                    "cluster_count": 1,
                    "analysis_count": 1,
                },
            },
            defect_candidates=[
                {
                    "cluster_id": "cl_001",
                    "title": "NPE in Service",
                    "severity": "HIGH",
                    "component": "checkout",
                    "owner_team": "checkout-backend",
                    "status": "pending",
                    "failure_category": "PRODUCT_BUG",
                    "evidence_bundle": {
                        "evidence_refs": [{"source": "stacktrace", "id": "st-1"}],
                    },
                }
            ],
            promoted_defects=[
                {
                    "defect_id": str(uuid.uuid4()),
                    "cluster_id": "cl_002",
                    "title": "Promoted timeout defect",
                    "severity": "CRITICAL",
                    "owner_team": "platform",
                    "status": "promoted",
                }
            ],
            ownership_resolutions={
                "cl_001": {
                    "service_name": "checkout",
                    "team_name": "checkout-backend",
                    "confidence": "high",
                    "match_source": "service_rule",
                },
            },
            structured_summary={
                "layer3_evidence_pack": {
                    "citations": [{"source": "summary", "excerpt": "NPE at Service.process"}],
                },
            },
        )

        with patch("app.services.agent_memory_service._index_memory_vector", new_callable=AsyncMock):
            count = await persist_pipeline_memory(
                db,
                state["project_id"],
                state["test_run_id"],
                state["pipeline_run_id"],
                state,
            )

        assert count >= 11
        added = [call[0][0] for call in db.add.call_args_list]
        by_type = {}
        for entry in added:
            by_type.setdefault(entry.entity_type, []).append(entry)

        assert by_type["evidence"]
        assert by_type["defect_candidate"][0].payload["owner_team"] == "checkout-backend"
        assert by_type["promoted_defect"][0].payload["promoted_defect_id"]
        assert by_type["release_input_snapshot"][0].payload["input_snapshot_sha256"]
        assert by_type["release_decision"][0].payload["input_snapshot_sha256"]
        assert by_type["ownership"][0].payload["ownership"]["team_name"] == "checkout-backend"


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
        assert results[0]["retrieval_audit"]["retrieval_version"] == "agent_memory.recall:v1"
        assert results[0]["retrieval_audit"]["project_id"] == str(entry.project_id)
        assert results[0]["retrieval_audit"]["rank"] == 1
        assert results[0]["memory_reference"]["memory_entry_id"] == str(entry.id)
        assert results[0]["memory_reference"]["retrieval_audit"]["rank"] == 1
        assert results[0]["memory_reference"]["payload_sha256"]

    @pytest.mark.asyncio
    async def test_no_matches_returns_empty(self):
        """No vector matches should produce an empty list."""
        from app.services.agent_memory_service import recall_similar

        with patch("app.services.agent_memory_service._query_similar_vectors", new_callable=AsyncMock) as mock_vec:
            mock_vec.return_value = []

            db = AsyncMock()
            results = await recall_similar(db, uuid.uuid4(), "some error", limit=5)

        assert results == []

    @pytest.mark.asyncio
    async def test_tie_scores_are_ordered_by_entry_id_for_replay(self):
        """Equal similarity matches should be returned in stable ID order."""
        from app.services.agent_memory_service import recall_similar

        project_id = uuid.uuid4()
        first_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        second_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
        first = _make_memory_entry(id=first_id, project_id=project_id)
        second = _make_memory_entry(id=second_id, project_id=project_id)

        with patch("app.services.agent_memory_service._query_similar_vectors", new_callable=AsyncMock) as mock_vec:
            mock_vec.return_value = [
                {"entry_id": str(second_id), "similarity": 0.91, "distance": 0.18},
                {"entry_id": str(first_id), "similarity": 0.91, "distance": 0.18},
            ]

            db = AsyncMock()
            db.execute = AsyncMock(return_value=MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[second, first])))
            ))

            results = await recall_similar(
                db, project_id, " NullPointerException   at line 42 ", limit=5,
            )

        assert [r["memory"].id for r in results] == [first_id, second_id]
        assert [r["retrieval_audit"]["rank"] for r in results] == [1, 2]
        assert results[0]["retrieval_audit"]["normalized_query_signature_sha256"]
        assert results[0]["retrieval_audit"]["distance"] == 0.18

    def test_build_memory_reference_has_canonical_shape(self):
        """Memory references should be stable pointers with payload checksums."""
        from app.services.agent_memory_service import build_memory_reference

        snapshot_id = uuid.uuid4()
        entry = _make_memory_entry(
            payload={
                "source_snapshot_id": str(snapshot_id),
                "label": "NPE in Service",
                "evidence_references": [{"source": "stacktrace", "id": "st-1"}],
            },
        )

        ref = build_memory_reference(
            entry,
            retrieval_audit={"retrieval_version": "agent_memory.recall:v1", "rank": 2},
        )

        assert ref["memory_entry_id"] == str(entry.id)
        assert ref["entity_type"] == "cluster"
        assert ref["entity_id"] == "cl_001"
        assert ref["source_snapshot_id"] == str(snapshot_id)
        assert ref["payload_sha256"]
        assert ref["retrieval_audit"]["rank"] == 2
        assert ref["evidence_refs"] == [{"source": "stacktrace", "id": "st-1"}]


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

    def test_normalizes_memory_signature_for_retrieval(self):
        from app.services.agent_memory_service import _normalize_memory_signature

        assert _normalize_memory_signature("  HTTP 500\n\tTimeout  ") == "http 500 timeout"


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

        resp = SimilarMemoryResponse(
            memory=mem_resp,
            similarity=0.92,
            retrieval_audit={"retrieval_version": "agent_memory.recall:v1"},
            memory_reference={
                "memory_entry_id": entry.id,
                "entity_type": entry.entity_type,
                "entity_id": entry.entity_id,
                "source_snapshot_id": None,
                "payload_sha256": "0" * 64,
                "retrieval_audit": {"retrieval_version": "agent_memory.recall:v1"},
                "evidence_refs": [],
            },
        )
        assert resp.similarity == 0.92
        assert resp.retrieval_audit["retrieval_version"] == "agent_memory.recall:v1"
        assert resp.memory_reference is not None
        assert resp.memory_reference.memory_entry_id == entry.id

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


class TestSnapshotMemoryReferences:
    """Tests for linking intelligence snapshots back to memory graph context."""

    @pytest.mark.asyncio
    async def test_save_snapshot_attaches_memory_reference_manifest(self):
        from app.services.intelligence_snapshot_service import save_snapshot

        run_id = uuid.uuid4()
        cluster = _make_memory_entry(
            run_id=run_id,
            entity_type="cluster",
            entity_id="cl_001",
            payload={"label": "NPE", "size": 2},
        )
        evidence = _make_memory_entry(
            run_id=run_id,
            entity_type="evidence",
            entity_id="ev_001",
            payload={"evidence_refs": [{"source": "stacktrace", "id": "st-1"}]},
        )

        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[
                evidence,
                cluster,
            ])))),
        ])

        await save_snapshot(
            db,
            run_id,
            {"run": {"id": str(run_id)}, "provenance": {}},
            fallback_used=False,
        )

        snapshot = db.add.call_args[0][0]
        manifest = snapshot.payload["memory_reference_manifest"]
        assert manifest["run_id"] == str(run_id)
        assert manifest["memory_reference_count"] == 2
        assert manifest["memory_graph_checksum_sha256"]
        assert len(manifest["memory_reference_ids"]) == 2
        assert [ref["entity_type"] for ref in manifest["memory_references"]] == [
            "cluster",
            "evidence",
        ]
        assert all(ref["source_snapshot_id"] == str(snapshot.id) for ref in manifest["memory_references"])
        assert snapshot.payload["provenance"]["memory_reference_count"] == 2
        assert snapshot.payload["provenance"]["memory_graph_checksum_sha256"] == manifest["memory_graph_checksum_sha256"]


class TestReplayMemoryReferences:
    """Tests for replay documents exposing deterministic memory references."""

    def test_replay_memory_references_are_sorted_and_hashed(self):
        from app.services.pipeline_replay_service import _memory_references_for_replay

        pipeline_id = uuid.uuid4()
        cluster = _make_memory_entry(
            id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            pipeline_run_id=pipeline_id,
            entity_type="cluster",
            entity_id="cl_002",
            payload={"label": "second"},
        )
        evidence = _make_memory_entry(
            id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            pipeline_run_id=pipeline_id,
            entity_type="evidence",
            entity_id="ev_001",
            payload={
                "evidence_refs": [{"source": "stacktrace", "id": "st-1"}],
                "retrieval_audit": {
                    "retrieval_version": "agent_memory.recall:v1",
                    "rank": 1,
                },
            },
        )

        refs = _memory_references_for_replay([evidence, cluster])

        assert [ref["entity_type"] for ref in refs] == ["cluster", "evidence"]
        assert refs[0]["memory_reference_id"]
        assert refs[1]["retrieval_audit_sha256"]
        assert refs[1]["retrieval_audit"]["rank"] == 1
        assert refs[1]["evidence_refs"] == [{"source": "stacktrace", "id": "st-1"}]


class TestMemoryConsumerWiring:
    """Tests for canonical memory consumers used by downstream agents."""

    @pytest.mark.asyncio
    async def test_release_risk_context_counts_open_defect_memories(self):
        from app.services.agent_memory_service import load_release_risk_memory_context

        project_id = uuid.uuid4()
        open_defect = _make_memory_entry(
            project_id=project_id,
            entity_type="promoted_defect",
            entity_id="defect-1",
            payload={"title": "Checkout timeout", "status": "open"},
        )
        closed_defect = _make_memory_entry(
            project_id=project_id,
            entity_type="defect_candidate",
            entity_id="defect-2",
            payload={"title": "Resolved issue", "status": "resolved"},
        )
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[
                closed_defect,
                open_defect,
            ])))
        ))

        context = await load_release_risk_memory_context(db, project_id)

        assert context["source"] == "agent_memory"
        assert context["open_defects"] == 1
        assert context["memory_entry_count"] == 2
        assert context["memory_references"][0]["entity_id"] == "defect-1"
        assert context["retrieval_audit"]["consumer"] == "release_risk"

    @pytest.mark.asyncio
    async def test_duplicate_defect_memory_uses_canonical_fallback(self):
        from app.services.agent_memory_service import find_duplicate_defect_memory

        project_id = uuid.uuid4()
        defect = _make_memory_entry(
            project_id=project_id,
            entity_type="promoted_defect",
            entity_id="defect-123",
            payload={
                "promoted_defect_id": "defect-123",
                "title": "Login page timeout on checkout flow",
                "status": "open",
            },
        )
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[defect])))
        ))

        with patch("app.services.agent_memory_service.recall_similar", new_callable=AsyncMock) as recall:
            recall.return_value = []
            result = await find_duplicate_defect_memory(
                db,
                project_id,
                "Login page timeout on checkout flow",
            )

        assert result["found"] is True
        assert result["duplicate_defect_id"] == "defect-123"
        assert result["memory_reference"]["entity_type"] == "promoted_defect"
        assert result["retrieval_audit"]["consumer"] == "defect_promotion_duplicate_check"

    @pytest.mark.asyncio
    async def test_ownership_resolution_reads_prior_memory(self):
        from app.services.agent_memory_service import resolve_ownership_from_memory

        project_id = uuid.uuid4()
        ownership = _make_memory_entry(
            project_id=project_id,
            entity_type="ownership",
            entity_id="cl_001",
            payload={
                "cluster_id": "cl_001",
                "member_test_ids": ["t1", "t2"],
                "ownership": {
                    "service_name": "checkout",
                    "team_name": "checkout-backend",
                    "confidence": "high",
                },
            },
        )
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[ownership])))
        ))

        context = await resolve_ownership_from_memory(
            db,
            project_id,
            cluster_id="cl_001",
            member_test_ids=["t2"],
        )

        assert context is not None
        assert context["ownership"]["team_name"] == "checkout-backend"
        assert context["memory_reference"]["entity_type"] == "ownership"
        assert context["retrieval_audit"]["consumer"] == "ownership_routing"


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
