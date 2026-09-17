"""Unit tests for AnalysisAgent — fallback, validation, sanitization, retry, priority."""
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("asyncpg")  # skip entire module if asyncpg is not installed

from app.agents.analysis_agent import AnalysisAgent  # noqa: E402
from app.services.category_normalizer import VALID_CATEGORIES  # noqa: E402


# ── Fallback analysis tests ──────────────────────────────────────────────────


class TestAnalysisAgentFallbacks:
    def test_build_timeout_analysis_sets_expected_flags(self):
        agent = AnalysisAgent()

        result = agent._build_timeout_analysis()

        assert result["failure_category"] == agent.UNKNOWN_CATEGORY
        assert result["timed_out"] is True
        assert result["requires_human_review"] is True
        assert result["confidence_score"] == 0
        assert result["tools_used"] == []

    def test_build_error_analysis_includes_error_message(self):
        agent = AnalysisAgent()

        result = agent._build_error_analysis(RuntimeError("llm offline"))

        assert result["failure_category"] == agent.UNKNOWN_CATEGORY
        assert result["requires_human_review"] is True
        assert result["error"] == "llm offline"
        assert "Analysis failed" in result["root_cause_summary"]
        assert result["tools_used"] == []


# ── Confidence validation tests ───────────────────────────────────────────────


class TestConfidenceValidation:
    def test_high_confidence_without_evidence_is_capped(self):
        agent = AnalysisAgent()
        analysis = {
            "confidence_score": 95,
            "evidence_references": [],
            "root_cause_summary": "This is a sufficiently detailed summary of the root cause analysis.",
            "tools_used": ["fetch_stacktrace"],
        }

        result = agent._validate_confidence(analysis)

        assert result["confidence_score"] <= 50
        assert result["confidence_validated"] is True

    def test_high_confidence_with_evidence_preserved(self):
        agent = AnalysisAgent()
        analysis = {
            "confidence_score": 90,
            "evidence_references": [
                {"source": "stacktrace", "reference_id": "abc", "excerpt": "NullPointerException"},
            ],
            "root_cause_summary": "The test failed due to a NullPointerException in the login service handler.",
            "tools_used": ["fetch_stacktrace"],
        }

        result = agent._validate_confidence(analysis)

        assert result["confidence_score"] == 90

    def test_short_summary_caps_confidence(self):
        agent = AnalysisAgent()
        analysis = {
            "confidence_score": 80,
            "evidence_references": [{"source": "test"}],
            "root_cause_summary": "Unknown error",
            "tools_used": ["fetch_stacktrace"],
        }

        result = agent._validate_confidence(analysis)

        assert result["confidence_score"] <= 30

    def test_no_tools_no_cache_caps_confidence(self):
        agent = AnalysisAgent()
        analysis = {
            "confidence_score": 85,
            "evidence_references": [{"source": "test"}],
            "root_cause_summary": "A detailed root cause analysis of the failure showing the error.",
            "tools_used": [],
        }

        result = agent._validate_confidence(analysis)

        assert result["confidence_score"] <= 60

    def test_cache_hit_bypasses_tool_penalty(self):
        agent = AnalysisAgent()
        analysis = {
            "confidence_score": 85,
            "evidence_references": [{"source": "test"}],
            "root_cause_summary": "A detailed root cause analysis of the failure showing the error.",
            "tools_used": [],
            "cache_hit": True,
        }

        result = agent._validate_confidence(analysis)

        assert result["confidence_score"] == 85

    def test_classified_by_bypasses_tool_penalty(self):
        agent = AnalysisAgent()
        analysis = {
            "confidence_score": 85,
            "evidence_references": [{"source": "test"}],
            "root_cause_summary": "A detailed root cause analysis of the failure showing the error.",
            "tools_used": [],
            "classified_by": "fast_classifier",
        }

        result = agent._validate_confidence(analysis)

        assert result["confidence_score"] == 85

    def test_multiple_evidence_bonus(self):
        agent = AnalysisAgent()
        analysis = {
            "confidence_score": 80,
            "evidence_references": [
                {"source": "stacktrace"},
                {"source": "splunk"},
                {"source": "ocp_events"},
            ],
            "root_cause_summary": "A detailed root cause analysis based on multiple sources.",
            "tools_used": ["fetch_stacktrace", "query_splunk", "analyze_ocp"],
        }

        result = agent._validate_confidence(analysis)

        assert result["confidence_score"] == 85  # 80 + 5 bonus

    def test_invalid_confidence_type_defaults_to_zero(self):
        agent = AnalysisAgent()
        analysis = {
            "confidence_score": "not_a_number",
            "evidence_references": [],
            "root_cause_summary": "Some summary",
        }

        result = agent._validate_confidence(analysis)

        assert result["confidence_score"] == 0

    def test_confidence_clamped_to_range(self):
        agent = AnalysisAgent()
        analysis = {
            "confidence_score": 150,
            "evidence_references": [{"source": "test"}],
            "root_cause_summary": "A detailed root cause analysis of the failure showing the error.",
            "tools_used": ["fetch_stacktrace"],
        }

        result = agent._validate_confidence(analysis)

        assert result["confidence_score"] <= 100

    def test_negative_confidence_clamped_to_zero(self):
        agent = AnalysisAgent()
        analysis = {
            "confidence_score": -10,
            "evidence_references": [],
            "root_cause_summary": "Short",
        }

        result = agent._validate_confidence(analysis)

        assert result["confidence_score"] == 0

    def test_requires_human_review_set_correctly(self):
        agent = AnalysisAgent()

        # Low confidence -> requires review
        low = agent._validate_confidence({
            "confidence_score": 30,
            "evidence_references": [{"source": "test"}],
            "root_cause_summary": "A sufficiently detailed summary of the root cause.",
            "tools_used": ["fetch_stacktrace"],
        })
        assert low["requires_human_review"] is True

        # High confidence -> no review needed
        high = agent._validate_confidence({
            "confidence_score": 90,
            "evidence_references": [{"source": "test"}],
            "root_cause_summary": "A sufficiently detailed summary of the root cause.",
            "tools_used": ["fetch_stacktrace"],
        })
        assert high["requires_human_review"] is False


# ── Category sanitization tests ───────────────────────────────────────────────


class TestCategorySanitization:
    def test_valid_category_preserved(self):
        agent = AnalysisAgent()
        for cat in VALID_CATEGORIES:
            result = agent._sanitize_category({"failure_category": cat})
            assert result["failure_category"] == cat

    def test_lowercase_category_normalized(self):
        agent = AnalysisAgent()
        result = agent._sanitize_category({"failure_category": "product_bug"})
        assert result["failure_category"] == "PRODUCT_BUG"

    def test_alias_resolved(self):
        agent = AnalysisAgent()

        aliases = {
            "BUG": "PRODUCT_BUG",
            "INFRA": "INFRASTRUCTURE",
            "DATA": "TEST_DATA",
            "AUTOMATION": "AUTOMATION_DEFECT",
            "INTERMITTENT": "FLAKY",
            "ENVIRONMENT": "INFRASTRUCTURE",
            "RACE_CONDITION": "FLAKY",
        }
        for alias, expected in aliases.items():
            result = agent._sanitize_category({"failure_category": alias})
            assert result["failure_category"] == expected, f"Alias {alias} should map to {expected}"
            assert result.get("_category_corrected_from") == alias

    def test_fuzzy_match_close_misspelling(self):
        agent = AnalysisAgent()
        result = agent._sanitize_category({"failure_category": "PRODUT_BUG"})
        assert result["failure_category"] == "PRODUCT_BUG"
        assert result.get("_category_corrected_from") == "PRODUT_BUG"

    def test_unrecognized_category_falls_back_to_unknown(self):
        agent = AnalysisAgent()
        result = agent._sanitize_category({"failure_category": "COMPLETELY_INVALID_XYZ"})
        assert result["failure_category"] == "UNKNOWN"
        assert result.get("_category_corrected_from") == "COMPLETELY_INVALID_XYZ"

    def test_empty_category_falls_back_to_unknown(self):
        agent = AnalysisAgent()
        result = agent._sanitize_category({"failure_category": ""})
        assert result["failure_category"] == "UNKNOWN"

    def test_none_category_falls_back_to_unknown(self):
        agent = AnalysisAgent()
        result = agent._sanitize_category({"failure_category": None})
        assert result["failure_category"] == "UNKNOWN"

    def test_whitespace_trimmed(self):
        agent = AnalysisAgent()
        result = agent._sanitize_category({"failure_category": "  FLAKY  "})
        assert result["failure_category"] == "FLAKY"


# ── Pattern-based heuristic tests ─────────────────────────────────────────────


class TestPatternBasedAnalysis:
    def test_oom_detected(self):
        agent = AnalysisAgent()
        result = agent._pattern_based_analysis("Container OOMKilled", "test_something")
        assert result["failure_category"] == "INFRASTRUCTURE"
        assert result["pod_issue_found"] is True
        assert result["classified_by"] == "pattern_heuristic"

    def test_connection_refused_detected(self):
        agent = AnalysisAgent()
        result = agent._pattern_based_analysis("Connection refused to localhost:8080", "test_api")
        assert result["failure_category"] == "INFRASTRUCTURE"

    def test_timeout_detected(self):
        agent = AnalysisAgent()
        result = agent._pattern_based_analysis("Request timed out after 30s", "test_slow")
        assert result["failure_category"] == "INFRASTRUCTURE"

    def test_404_detected(self):
        agent = AnalysisAgent()
        result = agent._pattern_based_analysis("HTTP 404 Not Found for /api/resource", "test_api")
        assert result["failure_category"] == "TEST_DATA"

    def test_null_pointer_detected(self):
        agent = AnalysisAgent()
        result = agent._pattern_based_analysis(
            "java.lang.NullPointerException at com.test.TestClass", "test_null"
        )
        assert result["failure_category"] == "AUTOMATION_DEFECT"

    def test_element_not_found_detected(self):
        agent = AnalysisAgent()
        result = agent._pattern_based_analysis("Element not found: #login-button", "test_ui")
        assert result["failure_category"] == "AUTOMATION_DEFECT"

    def test_assertion_detected(self):
        agent = AnalysisAgent()
        result = agent._pattern_based_analysis("AssertionError: expected 200 but was 500", "test_check")
        assert result["failure_category"] == "PRODUCT_BUG"

    def test_flaky_pattern_detected(self):
        agent = AnalysisAgent()
        result = agent._pattern_based_analysis("Intermittent failure: race condition in worker", "test_race")
        assert result["failure_category"] == "FLAKY"

    def test_no_pattern_returns_unknown(self):
        agent = AnalysisAgent()
        result = agent._pattern_based_analysis("Something completely unique happened", "test_mystery")
        assert result["failure_category"] == "UNKNOWN"
        assert result["confidence_score"] == 0

    def test_empty_error_returns_unknown(self):
        agent = AnalysisAgent()
        result = agent._pattern_based_analysis("", "test_empty")
        assert result["failure_category"] == "UNKNOWN"

    def test_none_error_returns_unknown(self):
        agent = AnalysisAgent()
        result = agent._pattern_based_analysis(None, "test_none")
        assert result["failure_category"] == "UNKNOWN"

    def test_setup_failed_detected(self):
        agent = AnalysisAgent()
        result = agent._pattern_based_analysis("setup failed: fixture db_connection raised", "test_setup")
        assert result["failure_category"] == "TEST_DATA"

    def test_502_detected(self):
        agent = AnalysisAgent()
        result = agent._pattern_based_analysis("HTTP 502 Bad Gateway", "test_gateway")
        assert result["failure_category"] == "INFRASTRUCTURE"


# ── Priority ordering tests ───────────────────────────────────────────────────


class TestPriorityOrdering:
    def test_blocker_before_normal(self):
        agent = AnalysisAgent()
        meta = {
            "tc-1": {"severity": "NORMAL", "historical_failure_count": 0},
            "tc-2": {"severity": "BLOCKER", "historical_failure_count": 0},
            "tc-3": {"severity": "MINOR", "historical_failure_count": 0},
        }

        result = agent._prioritize_tests(["tc-1", "tc-2", "tc-3"], meta)

        assert result[0] == "tc-2"  # BLOCKER first
        assert result[-1] == "tc-3"  # MINOR last

    def test_same_severity_ordered_by_failure_count(self):
        agent = AnalysisAgent()
        meta = {
            "tc-1": {"severity": "NORMAL", "historical_failure_count": 2},
            "tc-2": {"severity": "NORMAL", "historical_failure_count": 10},
            "tc-3": {"severity": "NORMAL", "historical_failure_count": 5},
        }

        result = agent._prioritize_tests(["tc-1", "tc-2", "tc-3"], meta)

        assert result[0] == "tc-2"  # Most failures first
        assert result[1] == "tc-3"
        assert result[2] == "tc-1"

    def test_missing_metadata_uses_defaults(self):
        agent = AnalysisAgent()
        meta = {}  # No metadata at all

        result = agent._prioritize_tests(["tc-1", "tc-2"], meta)

        assert set(result) == {"tc-1", "tc-2"}

    def test_none_severity_treated_as_normal(self):
        agent = AnalysisAgent()
        meta = {
            "tc-1": {"severity": None, "historical_failure_count": 0},
            "tc-2": {"severity": "BLOCKER", "historical_failure_count": 0},
        }

        result = agent._prioritize_tests(["tc-1", "tc-2"], meta)

        assert result[0] == "tc-2"


# ── _analyse_one integration tests ───────────────────────────────────────────


class TestAnalysisAgentAnalyzeOne:
    @pytest.mark.asyncio
    async def test_analyse_one_returns_timeout_fallback(self, monkeypatch):
        agent = AnalysisAgent()

        async def _raise_timeout(*_args, **_kwargs):
            raise asyncio.TimeoutError

        monkeypatch.setattr("app.agents.analysis_agent.run_triage_agent", _raise_timeout)

        # Mock the progressive fallback to return tier 3
        agent._build_progressive_fallback = AsyncMock(  # type: ignore[method-assign]
            return_value=agent._build_timeout_analysis()
        )

        result = await agent._analyse_one(
            asyncio.Semaphore(1),
            "tc-1",
            {"test_name": "test_login"},
            {"test_run_data": {}},
        )

        assert result["timed_out"] is True
        assert result["failure_category"] == agent.UNKNOWN_CATEGORY
        assert result["confidence_validated"] is True

    @pytest.mark.asyncio
    async def test_analyse_one_returns_error_fallback_on_exception(self, monkeypatch):
        agent = AnalysisAgent()

        async def _raise_error(*_args, **_kwargs):
            raise RuntimeError("provider failure")

        monkeypatch.setattr("app.agents.analysis_agent.run_triage_agent", _raise_error)

        result = await agent._analyse_one(
            asyncio.Semaphore(1),
            "tc-2",
            {"test_name": "test_checkout"},
            {"test_run_data": {}},
        )

        assert result["failure_category"] == agent.UNKNOWN_CATEGORY
        assert result["error"] == "provider failure"
        assert result["confidence_validated"] is True

    @pytest.mark.asyncio
    async def test_analyse_one_passes_error_message_to_triage(self, monkeypatch):
        """Verify that error_message and stack_trace from metadata are passed to run_triage_agent."""
        agent = AnalysisAgent()
        agent._upsert_analysis = AsyncMock()  # type: ignore[method-assign]

        captured_kwargs = {}

        async def _capture_triage(*_args, **kwargs):
            captured_kwargs.update(kwargs)
            return {
                "root_cause_summary": "Test failed due to NPE.",
                "failure_category": "PRODUCT_BUG",
                "confidence_score": 85,
                "evidence_references": [{"source": "stacktrace"}],
                "tools_used": ["fetch_stacktrace"],
            }

        monkeypatch.setattr("app.agents.analysis_agent.run_triage_agent", _capture_triage)

        await agent._analyse_one(
            asyncio.Semaphore(1),
            "tc-3",
            {
                "test_name": "test_login",
                "suite_name": "auth",
                "error_message": "NullPointerException",
                "stack_trace": "at com.app.Login.handle(Login.java:42)",
            },
            {"test_run_data": {}},
        )

        assert captured_kwargs["error_message"] == "NullPointerException"
        assert captured_kwargs["stack_trace"] == "at com.app.Login.handle(Login.java:42)"

    @pytest.mark.asyncio
    async def test_analyse_one_includes_audit_metadata(self, monkeypatch):
        agent = AnalysisAgent()
        agent._upsert_analysis = AsyncMock()  # type: ignore[method-assign]

        async def _mock_triage(*_args, **_kwargs):
            return {
                "root_cause_summary": "A reasonably detailed failure analysis.",
                "failure_category": "PRODUCT_BUG",
                "confidence_score": 75,
                "evidence_references": [{"source": "stacktrace"}],
                "tools_used": ["fetch_stacktrace"],
            }

        monkeypatch.setattr("app.agents.analysis_agent.run_triage_agent", _mock_triage)

        result = await agent._analyse_one(
            asyncio.Semaphore(1),
            "tc-4",
            {"test_name": "test_something", "severity": "BLOCKER", "error_message": "Error"},
            {"test_run_data": {}},
        )

        assert "_audit" in result
        assert result["_audit"]["test_severity"] == "BLOCKER"
        assert result["_audit"]["had_error_message"] is True
        assert isinstance(result["_audit"]["analysis_duration_seconds"], float)


# ── Retry logic tests ────────────────────────────────────────────────────────


class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_retry_on_low_confidence(self, monkeypatch):
        agent = AnalysisAgent()
        agent._upsert_analysis = AsyncMock()  # type: ignore[method-assign]

        call_count = 0

        async def _mock_triage(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            confidence = 20 if call_count == 1 else 75
            return {
                "root_cause_summary": "A detailed root cause analysis of this failure.",
                "failure_category": "PRODUCT_BUG",
                "confidence_score": confidence,
                "evidence_references": [{"source": "stacktrace"}],
                "tools_used": ["fetch_stacktrace"],
            }

        monkeypatch.setattr("app.agents.analysis_agent.run_triage_agent", _mock_triage)

        result = await agent._analyse_with_retry(
            asyncio.Semaphore(1),
            "tc-retry",
            {"test_name": "test_retry", "error_message": "NPE at Foo.java:42"},
            {"test_run_data": {}},
        )

        assert call_count == 2  # Retried once
        assert result["confidence_score"] == 75
        assert result.get("retry_count") == 1

    @pytest.mark.asyncio
    async def test_no_retry_on_sufficient_confidence(self, monkeypatch):
        agent = AnalysisAgent()
        agent._upsert_analysis = AsyncMock()  # type: ignore[method-assign]

        call_count = 0

        async def _mock_triage(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            return {
                "root_cause_summary": "A detailed root cause analysis of this failure.",
                "failure_category": "PRODUCT_BUG",
                "confidence_score": 85,
                "evidence_references": [{"source": "stacktrace"}],
                "tools_used": ["fetch_stacktrace"],
            }

        monkeypatch.setattr("app.agents.analysis_agent.run_triage_agent", _mock_triage)

        result = await agent._analyse_with_retry(
            asyncio.Semaphore(1),
            "tc-no-retry",
            {"test_name": "test_no_retry"},
            {"test_run_data": {}},
        )

        assert call_count == 1  # No retry
        assert result["confidence_score"] == 85

    @pytest.mark.asyncio
    async def test_no_retry_on_timeout(self, monkeypatch):
        agent = AnalysisAgent()
        agent._upsert_analysis = AsyncMock()  # type: ignore[method-assign]

        async def _raise_timeout(*_args, **_kwargs):
            raise asyncio.TimeoutError

        monkeypatch.setattr("app.agents.analysis_agent.run_triage_agent", _raise_timeout)
        agent._build_progressive_fallback = AsyncMock(  # type: ignore[method-assign]
            return_value={**agent._build_timeout_analysis(), "confidence_score": 0}
        )

        call_count = 0
        original_analyse = agent._analyse_one

        async def _counting_analyse(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return await original_analyse(*args, **kwargs)

        agent._analyse_one = _counting_analyse  # type: ignore[method-assign]

        result = await agent._analyse_with_retry(
            asyncio.Semaphore(1),
            "tc-timeout",
            {"test_name": "test_timeout"},
            {"test_run_data": {}},
        )

        assert call_count == 1  # No retry for timeouts
        assert result["timed_out"] is True

    @pytest.mark.asyncio
    async def test_retry_keeps_better_result(self, monkeypatch):
        """When retry produces worse confidence, keep the original."""
        agent = AnalysisAgent()
        agent._upsert_analysis = AsyncMock()  # type: ignore[method-assign]

        call_count = 0

        async def _mock_triage(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            # First call: 30, second call: 25 (worse)
            confidence = 30 if call_count == 1 else 25
            return {
                "root_cause_summary": "A detailed root cause analysis of this failure.",
                "failure_category": "PRODUCT_BUG",
                "confidence_score": confidence,
                "evidence_references": [{"source": "stacktrace"}],
                "tools_used": ["fetch_stacktrace"],
            }

        monkeypatch.setattr("app.agents.analysis_agent.run_triage_agent", _mock_triage)

        result = await agent._analyse_with_retry(
            asyncio.Semaphore(1),
            "tc-worse-retry",
            {"test_name": "test_worse_retry", "error_message": "AssertionError"},
            {"test_run_data": {}},
        )

        assert call_count == 2  # Did retry
        assert result["confidence_score"] == 30  # Kept original (after validation)
        assert result.get("retry_count") == 1


# ── Progressive fallback tests ────────────────────────────────────────────────


class TestProgressiveFallback:
    @pytest.mark.asyncio
    async def test_tier1_fast_classifier_success(self):
        agent = AnalysisAgent()

        mock_fc_module = MagicMock()
        mock_fc_module.FastClassifier.classify = AsyncMock(return_value={
            "root_cause_summary": "Infrastructure timeout detected.",
            "failure_category": "INFRASTRUCTURE",
            "confidence_score": 88,
            "evidence_references": [],
            "tools_used": [],
        })

        import sys
        # setitem restores the ORIGINAL module object on undo (re-audit E2);
        # popping it made the next import build a second copy.
        _modules = pytest.MonkeyPatch()
        _modules.setitem(sys.modules, "app.services.training.classifier", mock_fc_module)

        try:
            result = await agent._build_progressive_fallback(
                "tc-fb1",
                {"test_name": "test_something", "error_message": "Connection timed out"},
            )
            assert result["fallback_tier"] == 1
            assert result["fallback_reason"] == "timeout_fast_classifier"
        finally:
            _modules.undo()

    @pytest.mark.asyncio
    async def test_tier2_pattern_match_on_classifier_failure(self):
        agent = AnalysisAgent()

        mock_fc_module = MagicMock()
        mock_fc_module.FastClassifier.classify = AsyncMock(return_value=None)

        import sys
        # setitem restores the ORIGINAL module object on undo (re-audit E2);
        # popping it made the next import build a second copy.
        _modules = pytest.MonkeyPatch()
        _modules.setitem(sys.modules, "app.services.training.classifier", mock_fc_module)

        try:
            result = await agent._build_progressive_fallback(
                "tc-fb2",
                {"test_name": "test_oom", "error_message": "Container OOMKilled"},
            )
            assert result["fallback_tier"] == 2
            assert result["failure_category"] == "INFRASTRUCTURE"
        finally:
            _modules.undo()

    @pytest.mark.asyncio
    async def test_tier3_generic_when_no_pattern(self):
        agent = AnalysisAgent()

        mock_fc_module = MagicMock()
        mock_fc_module.FastClassifier.classify = AsyncMock(return_value=None)

        import sys
        # setitem restores the ORIGINAL module object on undo (re-audit E2);
        # popping it made the next import build a second copy.
        _modules = pytest.MonkeyPatch()
        _modules.setitem(sys.modules, "app.services.training.classifier", mock_fc_module)

        try:
            result = await agent._build_progressive_fallback(
                "tc-fb3",
                {"test_name": "test_unknown", "error_message": "xyzzy"},
            )
            assert result["fallback_tier"] == 3
            assert result["failure_category"] == "UNKNOWN"
        finally:
            _modules.undo()


# ── Full run method tests ─────────────────────────────────────────────────────


class TestAnalysisAgentRun:
    @pytest.mark.asyncio
    async def test_frozen_max_failures_limits_the_prioritized_analysis_scope(self, monkeypatch):
        from app.services.llm_cost_budget import CapDecision

        agent = AnalysisAgent()
        agent.mark_stage_running = AsyncMock()  # type: ignore[method-assign]
        agent.mark_stage_done = AsyncMock()  # type: ignore[method-assign]
        agent.broadcast_progress = AsyncMock()  # type: ignore[method-assign]
        agent.log_decision = AsyncMock()  # type: ignore[method-assign]
        agent._fetch_test_metadata = AsyncMock(return_value={  # type: ignore[method-assign]
            "normal": {"test_name": "normal", "severity": "NORMAL"},
            "blocker": {"test_name": "blocker", "severity": "BLOCKER"},
            "critical": {"test_name": "critical", "severity": "CRITICAL"},
        })
        agent._fetch_human_corrections = AsyncMock(return_value={})  # type: ignore[method-assign]
        agent._resolve_adaptive_concurrency = AsyncMock(return_value={  # type: ignore[method-assign]
            "concurrency": 1,
            "rationale": "test",
        })
        analysed: list[str] = []

        async def _analyse(_semaphore, tc_id, _meta, _state):
            analysed.append(tc_id)
            return {
                "root_cause_summary": f"Analysis for {tc_id} with enough detail.",
                "failure_category": "UNKNOWN",
                "confidence_score": 0,
                "requires_human_review": True,
                "evidence_references": [],
                "tools_used": [],
                "_audit": {},
            }

        agent._analyse_with_retry = _analyse  # type: ignore[method-assign]
        agent._batch_upsert_analyses = AsyncMock()  # type: ignore[method-assign]
        agent._record_latency_feedback = MagicMock()  # type: ignore[method-assign]
        monkeypatch.setattr(
            "app.services.llm_cost_budget.check_and_apply_cap",
            AsyncMock(return_value=CapDecision(action="ALLOW", rationale="within budget")),
        )
        state = {
            "pipeline_run_id": "pipeline-1",
            "project_id": "symbolic-project",
            "failed_test_ids": ["normal", "blocker", "critical"],
            "resolved_agent_configs": {
                "agent.root_cause_analysis.v1": {
                    "config": {"thresholds": {"max_failures_analyzed": 2}}
                }
            },
        }

        result = await agent.run(state)

        assert analysed == ["blocker", "critical"]
        assert set(result["analyses"]) == {"blocker", "critical"}
        assert result["stage_quality"] == "degraded"
        assert any("max_failures_analyzed=2" in error for error in result["errors"])
        assert agent.mark_stage_done.await_args.kwargs["result_data"]["config_skipped"] == 1

    @pytest.mark.asyncio
    async def test_durable_pipeline_honors_hard_cost_cap(self, monkeypatch):
        from app.services.llm_cost_budget import CapDecision

        agent = AnalysisAgent()
        agent.mark_stage_running = AsyncMock()  # type: ignore[method-assign]
        agent.mark_stage_done = AsyncMock()  # type: ignore[method-assign]
        agent.broadcast_progress = AsyncMock()  # type: ignore[method-assign]
        agent.log_decision = AsyncMock()  # type: ignore[method-assign]
        agent._analyse_one = AsyncMock()  # type: ignore[method-assign]
        monkeypatch.setattr(
            "app.services.llm_cost_budget.check_and_apply_cap",
            AsyncMock(
                return_value=CapDecision(
                    action="HARD_BLOCK",
                    block=True,
                    rationale="daily cap reached",
                )
            ),
        )
        state = {
            "pipeline_run_id": "pipe-budget-block",
            "project_id": str(uuid.uuid4()),
            "failed_test_ids": ["tc-1"],
        }

        result = await agent.run(state)

        agent._analyse_one.assert_not_awaited()
        assert result["stage_quality"] == "cost_budget_blocked"
        assert state["_cost_budget_block"] is True

    @pytest.mark.asyncio
    async def test_durable_pipeline_threads_cost_downgrade_into_state(self, monkeypatch):
        from app.services.llm_cost_budget import CapDecision

        agent = AnalysisAgent()
        agent.mark_stage_running = AsyncMock()  # type: ignore[method-assign]
        agent.mark_stage_done = AsyncMock()  # type: ignore[method-assign]
        agent.broadcast_progress = AsyncMock()  # type: ignore[method-assign]
        agent.log_decision = AsyncMock()  # type: ignore[method-assign]
        monkeypatch.setattr(
            "app.services.llm_cost_budget.check_and_apply_cap",
            AsyncMock(
                return_value=CapDecision(
                    action="AUTO_DOWNGRADE_TO_RULES",
                    mode_override="rules",
                    rationale="soft cap reached",
                )
            ),
        )
        state = {
            "pipeline_run_id": "pipe-budget-rules",
            "project_id": str(uuid.uuid4()),
            "failed_test_ids": [],
        }

        await agent.run(state)

        assert state["_cost_budget_mode_override"] == "rules"

    @pytest.mark.asyncio
    async def test_run_with_empty_failed_ids(self):
        agent = AnalysisAgent()
        agent.mark_stage_running = AsyncMock()  # type: ignore[method-assign]
        agent.mark_stage_done = AsyncMock()  # type: ignore[method-assign]
        agent.broadcast_progress = AsyncMock()  # type: ignore[method-assign]

        state = {
            "pipeline_run_id": "pipe-1",
            "project_id": "proj-1",
            "failed_test_ids": [],
        }

        result = await agent.run(state)

        assert result["analyses"] == {}
        assert "root_cause_analysis" in result["completed_stages"]
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_run_handles_all_exceptions(self, monkeypatch):
        agent = AnalysisAgent()
        agent.mark_stage_running = AsyncMock()  # type: ignore[method-assign]
        agent.mark_stage_done = AsyncMock()  # type: ignore[method-assign]
        agent.broadcast_progress = AsyncMock()  # type: ignore[method-assign]
        agent._fetch_test_metadata = AsyncMock(return_value={})  # type: ignore[method-assign]

        async def _raise(*_args, **_kwargs):
            raise RuntimeError("total failure")

        monkeypatch.setattr("app.agents.analysis_agent.run_triage_agent", _raise)
        agent._upsert_analysis = AsyncMock()  # type: ignore[method-assign]

        state = {
            "pipeline_run_id": "pipe-2",
            "project_id": "proj-2",
            "failed_test_ids": ["tc-a", "tc-b"],
        }

        result = await agent.run(state)

        assert len(result["analyses"]) == 2
        # Both should have error fallbacks (confidence validated + category sanitized)
        for tc_id in ["tc-a", "tc-b"]:
            assert result["analyses"][tc_id]["failure_category"] == "UNKNOWN"


# ── Edge case tests ───────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_sanitize_category_with_mixed_case_and_spaces(self):
        agent = AnalysisAgent()
        result = agent._sanitize_category({"failure_category": "  Product_Bug  "})
        assert result["failure_category"] == "PRODUCT_BUG"

    def test_validate_confidence_with_missing_fields(self):
        """Confidence validation should handle missing optional fields gracefully."""
        agent = AnalysisAgent()
        result = agent._validate_confidence({})
        assert result["confidence_score"] == 0
        assert result["requires_human_review"] is True

    def test_pattern_analysis_case_insensitive(self):
        agent = AnalysisAgent()
        result = agent._pattern_based_analysis("OOMKILLED in pod-xyz", "test_oom")
        assert result["failure_category"] == "INFRASTRUCTURE"

    def test_priority_with_single_test(self):
        agent = AnalysisAgent()
        result = agent._prioritize_tests(["tc-1"], {"tc-1": {"severity": "NORMAL"}})
        assert result == ["tc-1"]

    def test_priority_with_empty_list(self):
        agent = AnalysisAgent()
        result = agent._prioritize_tests([], {})
        assert result == []

    def test_build_error_analysis_with_long_exception(self):
        agent = AnalysisAgent()
        long_msg = "x" * 10000
        result = agent._build_error_analysis(RuntimeError(long_msg))
        assert result["error"] == long_msg
        assert result["requires_human_review"] is True

    def test_validate_confidence_zero_remains_zero(self):
        agent = AnalysisAgent()
        result = agent._validate_confidence({
            "confidence_score": 0,
            "evidence_references": [],
            "root_cause_summary": "",
        })
        assert result["confidence_score"] == 0

    @pytest.mark.asyncio
    async def test_analyse_one_returns_valid_result(self, monkeypatch):
        """Analysis results should be returned with validation applied."""
        agent = AnalysisAgent()

        async def _mock_triage(*_args, **_kwargs):
            return {
                "root_cause_summary": "A detailed root cause analysis of this failure.",
                "failure_category": "PRODUCT_BUG",
                "confidence_score": 80,
                "evidence_references": [{"source": "stacktrace"}],
                "tools_used": ["fetch_stacktrace"],
            }

        monkeypatch.setattr("app.agents.analysis_agent.run_triage_agent", _mock_triage)

        result = await agent._analyse_one(
            asyncio.Semaphore(1),
            "tc-valid",
            {"test_name": "test_valid"},
            {"test_run_data": {}},
        )

        assert result["failure_category"] == "PRODUCT_BUG"
        assert result["confidence_validated"] is True
        assert "_audit" in result
