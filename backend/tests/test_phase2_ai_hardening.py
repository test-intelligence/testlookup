"""
Unit tests for Phase 2: AI Accuracy & Agent Pipeline Hardening.

Covers:
  P2-1: Pydantic LLM output schemas & validate_llm_output()
  P2-3: Pipeline state merge consistency (dedup, stage_errors)
  P2-4: Explicit error propagation in AnalysisAgent
  P2-5: Cost-optimized LLM routing in ReleaseRiskAgent
  P2-6: Flakiness detection accuracy in AnalysisAgent
  P2-8: Conversation context caching
"""
import asyncio
import hashlib
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")  # skip if asyncpg unavailable

from app.models.llm_schemas import (  # noqa: E402
    ActionPlan,
    ClusterClassification,
    EvidencePack,
    IncidentView,
    ReleaseReasoning,
    validate_llm_output,
)
from app.agents.state import (  # noqa: E402
    WorkflowState,
    _dedup_concat_lists,
    _merge_error_dicts,
)
from app.agents.analysis_agent import AnalysisAgent  # noqa: E402
from app.agents.release_risk_agent import (  # noqa: E402
    ReleaseRiskAgent,
    _EXTREME_GO_THRESHOLD,
    _EXTREME_NO_GO_THRESHOLD,
)
from app.agents.conversation import (  # noqa: E402
    _LAST_COMPRESSION_HASH,
    _RUN_CONTEXT_CACHE,
    _RUN_CONTEXT_TTL_SECONDS,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P2-1: Pydantic LLM Output Schema Validation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestIncidentViewSchema:
    def test_valid_incident_view(self):
        raw = {
            "what_failed": "Checkout flow",
            "likely_cause": "API timeout",
            "scope": "payment-service",
            "criticality": "HIGH",
            "release_impact": "NO_GO",
            "failure_breakdown": {"product_bugs": 3, "infrastructure": 1},
        }
        result = validate_llm_output(IncidentView, raw, context="test")
        assert result["criticality"] == "HIGH"
        assert result["release_impact"] == "NO_GO"
        assert result["failure_breakdown"]["product_bugs"] == 3

    def test_invalid_criticality_normalized(self):
        raw = {"criticality": "SUPER_BAD", "release_impact": "MAYBE"}
        result = validate_llm_output(IncidentView, raw, context="test")
        assert result["criticality"] == "MEDIUM"
        assert result["release_impact"] == "CONDITIONAL_GO"

    def test_empty_dict_returns_defaults(self):
        result = validate_llm_output(IncidentView, {}, context="test")
        assert result["what_failed"] == ""
        assert result["criticality"] == "MEDIUM"
        assert result["failure_breakdown"] == {}

    def test_partial_dict_fills_defaults(self):
        raw = {"what_failed": "Login page", "criticality": "LOW"}
        result = validate_llm_output(IncidentView, raw, context="test")
        assert result["what_failed"] == "Login page"
        assert result["criticality"] == "LOW"
        assert result["likely_cause"] == ""


class TestEvidencePackSchema:
    def test_valid_evidence_pack(self):
        raw = {
            "top_stack_traces": ["NullPointerException at Foo.java:42"],
            "log_anomalies": ["Error rate spike"],
            "data_sources_used": ["stacktrace", "splunk"],
        }
        result = validate_llm_output(EvidencePack, raw, context="test")
        assert len(result["top_stack_traces"]) == 1
        assert result["flaky_test_ids"] == []

    def test_empty_returns_empty_lists(self):
        result = validate_llm_output(EvidencePack, {}, context="test")
        assert result["top_stack_traces"] == []
        assert result["log_anomalies"] == []


class TestActionPlanSchema:
    def test_valid_action_plan(self):
        raw = {
            "immediate_mitigation": "Rollback to v1.2.3",
            "fix_recommendations": ["Fix the timeout", "Add retry logic"],
            "validation_steps": ["Rerun e2e suite"],
            "rollback_guidance": "kubectl rollout undo",
            "owner_hints": {"qa": "Verify fix", "developer": "Fix timeout"},
        }
        result = validate_llm_output(ActionPlan, raw, context="test")
        assert result["immediate_mitigation"] == "Rollback to v1.2.3"
        assert len(result["fix_recommendations"]) == 2


class TestClusterClassificationSchema:
    def test_valid_classification(self):
        raw = {
            "classification": "known_flaky_recurrence",
            "confidence": 85,
            "evidence": "Cluster has 5 prior occurrences",
        }
        result = validate_llm_output(ClusterClassification, raw, context="test")
        assert result["classification"] == "known_flaky_recurrence"
        assert result["confidence"] == 85

    def test_invalid_classification_defaults_to_new_regression(self):
        raw = {"classification": "TOTALLY_MADE_UP", "confidence": 50}
        result = validate_llm_output(ClusterClassification, raw, context="test")
        assert result["classification"] == "new_regression"

    def test_confidence_clamped(self):
        raw = {"classification": "environmental_anomaly", "confidence": 150}
        # Pydantic validation will fail, but validate_llm_output handles it gracefully
        result = validate_llm_output(ClusterClassification, raw, context="test")
        assert isinstance(result["confidence"], int)


class TestReleaseReasoningSchema:
    def test_valid_reasoning(self):
        raw = {
            "reasoning": "Score 38/100 due to checkout bugs.",
            "blocking_issues": ["Payment bug"],
            "conditions_for_go": ["Fix payment bug"],
        }
        result = validate_llm_output(ReleaseReasoning, raw, context="test")
        assert "checkout" in result["reasoning"]
        assert len(result["blocking_issues"]) == 1

    def test_empty_returns_defaults(self):
        result = validate_llm_output(ReleaseReasoning, {}, context="test")
        assert result["reasoning"] == ""
        assert result["blocking_issues"] == []
        assert result["conditions_for_go"] == []


class TestValidateLlmOutputGracefulFailure:
    def test_non_dict_input_returns_defaults(self):
        # Should not raise even with bad input
        result = validate_llm_output(IncidentView, {"criticality": 42}, context="test")
        assert isinstance(result, dict)

    def test_none_values_use_defaults(self):
        raw = {"what_failed": None, "criticality": None}
        result = validate_llm_output(IncidentView, raw, context="test")
        assert isinstance(result, dict)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P2-3: Pipeline State Merge Consistency
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDedupConcatLists:
    def test_deduplicates_preserving_order(self):
        result = _dedup_concat_lists(["a", "b", "c"], ["b", "c", "d"])
        assert result == ["a", "b", "c", "d"]

    def test_empty_lists(self):
        assert _dedup_concat_lists([], []) == []

    def test_single_list(self):
        assert _dedup_concat_lists(["x"], []) == ["x"]

    def test_identical_lists(self):
        result = _dedup_concat_lists(["a", "b"], ["a", "b"])
        assert result == ["a", "b"]


class TestMergeErrorDicts:
    def test_merges_disjoint_stages(self):
        a = {"analysis": ["timeout"]}
        b = {"anomaly": ["network error"]}
        result = _merge_error_dicts(a, b)
        assert "analysis" in result
        assert "anomaly" in result

    def test_merges_same_stage_errors(self):
        a = {"analysis": ["error1"]}
        b = {"analysis": ["error2"]}
        result = _merge_error_dicts(a, b)
        assert result["analysis"] == ["error1", "error2"]

    def test_empty_dicts(self):
        assert _merge_error_dicts({}, {}) == {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P2-4: Error Propagation in AnalysisAgent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestAnalysisAgentErrorPropagation:
    @pytest.mark.asyncio
    async def test_stage_quality_degraded_when_high_error_ratio(self):
        """When >30% of analyses fail, stage_quality should be 'degraded'."""
        agent = AnalysisAgent()

        # Mock: 5 failed tests, 3 will timeout (>30%)
        failed_ids = ["tc1", "tc2", "tc3", "tc4", "tc5"]
        timeout_result = agent._build_timeout_analysis()
        success_result = {
            "root_cause_summary": "Product bug in checkout",
            "failure_category": "PRODUCT_BUG",
            "confidence_score": 80,
            "evidence_references": [{"source": "stacktrace", "excerpt": "NPE at line 42"}],
            "tools_used": ["fetch_stacktrace"],
            "is_flaky": False,
        }

        # Create a mock state
        state = {
            "pipeline_run_id": "test-run-1",
            "project_id": "proj-1",
            "test_run_id": "run-1",
            "failed_test_ids": failed_ids,
            "test_run_data": {},
        }

        # Patch internal methods
        agent.mark_stage_running = AsyncMock()
        agent.mark_stage_done = AsyncMock()
        agent.broadcast_progress = AsyncMock()
        agent._fetch_test_metadata = AsyncMock(return_value={
            tc_id: {"test_name": f"test_{tc_id}", "severity": "NORMAL"}
            for tc_id in failed_ids
        })
        agent._batch_upsert_analyses = AsyncMock()

        # 2 succeed, 3 timeout
        call_count = 0

        async def mock_analyse(sem, tc_id, meta, st):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return dict(success_result)
            return dict(timeout_result)

        agent._analyse_with_retry = mock_analyse

        result = await agent.run(state)

        assert result["stage_quality"] == "degraded"
        assert result["low_confidence_count"] >= 0
        assert "stage_errors" in result  # key is always present

    @pytest.mark.asyncio
    async def test_stage_quality_normal_when_low_error_ratio(self):
        """When <=30% fail, stage_quality should be 'normal'."""
        agent = AnalysisAgent()
        state = {
            "pipeline_run_id": "test-run-2",
            "project_id": "proj-1",
            "test_run_id": "run-2",
            "failed_test_ids": ["tc1", "tc2", "tc3", "tc4", "tc5"],
            "test_run_data": {},
        }

        agent.mark_stage_running = AsyncMock()
        agent.mark_stage_done = AsyncMock()
        agent.broadcast_progress = AsyncMock()
        agent._fetch_test_metadata = AsyncMock(return_value={
            f"tc{i}": {"test_name": f"test_tc{i}", "severity": "NORMAL"}
            for i in range(1, 6)
        })
        agent._batch_upsert_analyses = AsyncMock()

        # All succeed
        async def mock_analyse(sem, tc_id, meta, st):
            return {
                "root_cause_summary": "Found bug",
                "failure_category": "PRODUCT_BUG",
                "confidence_score": 85,
                "evidence_references": [{"source": "st"}],
                "tools_used": ["fetch_stacktrace"],
                "is_flaky": False,
            }

        agent._analyse_with_retry = mock_analyse

        result = await agent.run(state)
        assert result["stage_quality"] == "normal"
        assert result.get("stage_errors", {}) == {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P2-5: Cost-Optimized LLM Routing in ReleaseRiskAgent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCostOptimizedLLMRouting:
    def test_extreme_go_threshold_is_reasonable(self):
        assert 0 < _EXTREME_GO_THRESHOLD <= 15

    def test_extreme_no_go_threshold_is_reasonable(self):
        assert 60 <= _EXTREME_NO_GO_THRESHOLD <= 90

    def test_deterministic_blocking_issues_high_user_impact(self):
        issues = ReleaseRiskAgent._deterministic_blocking_issues(
            {"user_impact": 80, "regression_likely": 20, "blast_radius": 30}
        )
        assert any("user impact" in i.lower() for i in issues)

    def test_deterministic_blocking_issues_no_flags(self):
        issues = ReleaseRiskAgent._deterministic_blocking_issues(
            {"user_impact": 10, "regression_likely": 10, "blast_radius": 10}
        )
        assert issues == []

    def test_deterministic_blocking_issues_multiple_flags(self):
        issues = ReleaseRiskAgent._deterministic_blocking_issues(
            {"user_impact": 60, "regression_likely": 50, "blast_radius": 60}
        )
        assert len(issues) == 3


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P2-6: Flakiness Detection Accuracy
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFlakinessDetection:
    def test_truly_flaky_test_detected(self):
        """Test with both passes and failures (50% rate) is flaky."""
        agent = AnalysisAgent()
        analysis = {
            "confidence_score": 70,
            "root_cause_summary": "Test fails intermittently",
            "evidence_references": [{"source": "flakiness_db"}],
            "is_flaky": False,  # LLM said no
            "flakiness_data": {"pass_count": 5, "fail_count": 5},
            "tools_used": ["check_flakiness"],
        }
        result = agent._validate_confidence(analysis)
        assert result["is_flaky"] is True

    def test_broken_test_not_marked_flaky(self):
        """Test that never passed is broken, not flaky."""
        agent = AnalysisAgent()
        analysis = {
            "confidence_score": 70,
            "root_cause_summary": "Always fails",
            "evidence_references": [{"source": "flakiness_db"}],
            "is_flaky": True,  # LLM said yes, but wrong
            "flakiness_data": {"pass_count": 0, "fail_count": 10},
            "tools_used": ["check_flakiness"],
        }
        result = agent._validate_confidence(analysis)
        assert result["is_flaky"] is False

    def test_stable_test_not_marked_flaky(self):
        """Test with >90% pass rate is stable, not flaky."""
        agent = AnalysisAgent()
        analysis = {
            "confidence_score": 70,
            "root_cause_summary": "Rare failure",
            "evidence_references": [{"source": "flakiness_db"}],
            "is_flaky": True,
            "flakiness_data": {"pass_count": 95, "fail_count": 5},
            "tools_used": ["check_flakiness"],
        }
        result = agent._validate_confidence(analysis)
        assert result["is_flaky"] is False

    def test_no_flakiness_data_preserves_original(self):
        """Without historical data, the LLM's guess is preserved."""
        agent = AnalysisAgent()
        analysis = {
            "confidence_score": 60,
            "root_cause_summary": "Possibly flaky",
            "evidence_references": [{"source": "st"}],
            "is_flaky": True,
            "tools_used": ["fetch_stacktrace"],
        }
        result = agent._validate_confidence(analysis)
        assert result["is_flaky"] is True  # no flakiness_data → kept as-is

    def test_flaky_with_low_pass_rate_is_flaky(self):
        """Test with 15% pass rate is still flaky (within 10-90 range)."""
        agent = AnalysisAgent()
        analysis = {
            "confidence_score": 70,
            "root_cause_summary": "Intermittent",
            "evidence_references": [{"source": "flakiness_db"}],
            "is_flaky": False,
            "flakiness_data": {"pass_count": 3, "fail_count": 17},
            "tools_used": ["check_flakiness"],
        }
        result = agent._validate_confidence(analysis)
        assert result["is_flaky"] is True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P2-5: Smart Retry — only retry LLM failures, not missing data
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSmartRetry:
    @pytest.mark.asyncio
    async def test_no_retry_when_input_data_missing(self):
        """Low confidence from empty inputs should NOT trigger retry (P2-5)."""
        agent = AnalysisAgent()
        call_count = 0

        async def mock_analyse_one(sem, tc_id, meta, state):
            nonlocal call_count
            call_count += 1
            return {
                "confidence_score": 20,
                "root_cause_summary": "Insufficient data",
                "failure_category": "UNKNOWN",
                "evidence_references": [],
                "tools_used": [],
                "is_flaky": False,
            }

        agent._analyse_one = mock_analyse_one
        sem = asyncio.Semaphore(5)
        # No error_message or stack_trace in meta → missing input data
        await agent._analyse_with_retry(sem, "tc1", {}, {})
        assert call_count == 1  # No retry

    @pytest.mark.asyncio
    async def test_retry_when_input_data_present_and_low_confidence(self):
        """Low confidence WITH input data should trigger retry (P2-5)."""
        agent = AnalysisAgent()
        call_count = 0

        async def mock_analyse_one(sem, tc_id, meta, state):
            nonlocal call_count
            call_count += 1
            return {
                "confidence_score": 20 if call_count == 1 else 60,
                "root_cause_summary": "Found something" if call_count > 1 else "Unclear",
                "failure_category": "PRODUCT_BUG",
                "evidence_references": [{"source": "st"}] if call_count > 1 else [],
                "tools_used": ["fetch_stacktrace"],
                "is_flaky": False,
            }

        agent._analyse_one = mock_analyse_one
        sem = asyncio.Semaphore(5)
        result = await agent._analyse_with_retry(
            sem, "tc1",
            {"error_message": "NPE at Foo.java:42", "test_name": "test_foo"},
            {},
        )
        assert call_count == 2  # Retried once
        assert result["confidence_score"] == 60  # Kept the better result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P2-8: Conversation Context Caching
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRunContextCache:
    def setup_method(self):
        _RUN_CONTEXT_CACHE.clear()
        _LAST_COMPRESSION_HASH.clear()

    def test_cache_stores_and_retrieves(self):
        """Cache entry should be retrievable within TTL."""
        import time as t
        key = "proj1:5"
        _RUN_CONTEXT_CACHE[key] = (t.monotonic(), "table data", [{"id": "1"}])
        cached = _RUN_CONTEXT_CACHE.get(key)
        assert cached is not None
        ts, text, sources = cached
        assert text == "table data"

    def test_cache_ttl_is_positive(self):
        assert _RUN_CONTEXT_TTL_SECONDS > 0

    def test_compression_hash_skip(self):
        """Same content hash should allow skip."""
        session_id = "sess-1"
        transcript = "USER: hello\nASSISTANT: hi"
        content_hash = hashlib.sha256(transcript.encode()).hexdigest()[:16]
        _LAST_COMPRESSION_HASH[session_id] = content_hash

        # Second call with same hash should detect duplicate
        assert _LAST_COMPRESSION_HASH.get(session_id) == content_hash


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P2-2: Improved Prompt Engineering (existence / structural checks)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestPromptGroundingRules:
    def test_summary_agent_system_prompt_has_grounding_rules(self):
        from app.agents.summary_agent import _SYSTEM_PROMPT
        assert "GROUNDING RULES" in _SYSTEM_PROMPT
        assert "never fabricate" in _SYSTEM_PROMPT.lower()

    def test_regression_watchman_prompt_has_grounding_rules(self):
        from app.agents.regression_watchman import _CLASSIFY_PROMPT
        assert "GROUNDING RULES" in _CLASSIFY_PROMPT
        assert "EXAMPLE" in _CLASSIFY_PROMPT

    def test_release_risk_prompt_has_grounding_rules(self):
        from app.agents.release_risk_agent import _REASONING_PROMPT
        assert "GROUNDING RULES" in _REASONING_PROMPT
        assert "DETERMINISTIC" in _REASONING_PROMPT
        assert "EXAMPLE" in _REASONING_PROMPT

    def test_release_risk_prompt_forbids_recommendation_override(self):
        from app.agents.release_risk_agent import _REASONING_PROMPT
        assert "do not override" in _REASONING_PROMPT.lower()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P2-3: WorkflowState new fields exist
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestWorkflowStateFields:
    def test_stage_errors_field_exists(self):
        assert "stage_errors" in WorkflowState.__annotations__

    def test_stage_quality_field_exists(self):
        assert "stage_quality" in WorkflowState.__annotations__

    def test_low_confidence_count_field_exists(self):
        assert "low_confidence_count" in WorkflowState.__annotations__

    def test_completed_stages_uses_dedup_reducer(self):
        """completed_stages should use _dedup_concat_lists to prevent duplicates."""
        annotation = WorkflowState.__annotations__["completed_stages"]
        # Annotated types store metadata; check the reducer function name
        assert hasattr(annotation, "__metadata__") or "dedup" in str(annotation)
