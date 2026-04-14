"""
Comprehensive tests for Agent Reliability Hardening Sprint.
Covers: WS1 (correctness), WS2 (idempotency), WS3 (performance),
        WS4 (redaction), WS5 (accuracy), WS6 (observability).
"""

import pytest

pytest.importorskip("asyncpg")

from app.core.config import settings  # noqa: E402

# ============================================================================
# WS1: LLM JSON Parser tests
# ============================================================================

from app.services.llm_json_parser import parse_llm_json  # noqa: E402


class TestLLMJsonParser:
    def test_valid_json(self):
        raw = '{"reasoning": "test", "blocking_issues": [], "conditions_for_go": []}'
        parsed, error = parse_llm_json(raw, context="test")
        assert error is None
        assert parsed["reasoning"] == "test"

    def test_fenced_json(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        parsed, error = parse_llm_json(raw, context="test")
        assert error is None
        assert parsed["key"] == "value"

    def test_prose_plus_json(self):
        raw = "Here is my analysis:\n{\"key\": \"value\"}\nEnd of response."
        parsed, error = parse_llm_json(raw, context="test")
        assert error is None
        assert parsed["key"] == "value"

    def test_malformed_json_returns_fallback(self):
        raw = "{key: value, broken}"
        fallback = {"default": True}
        parsed, error = parse_llm_json(raw, fallback=fallback, context="test")
        assert error == "invalid_json"
        assert parsed["default"] is True

    def test_empty_response(self):
        parsed, error = parse_llm_json("", context="test")
        assert error == "empty_response"
        assert parsed == {}

    def test_schema_validation_passes(self):
        raw = '{"a": 1, "b": 2, "c": 3}'
        parsed, error = parse_llm_json(raw, expected_keys=["a", "b", "c"], context="test")
        assert error is None

    def test_schema_partial_mismatch_fills_defaults(self):
        """When only 1 key of 4 is missing, it should auto-fill."""
        raw = '{"reasoning": "yes", "blocking_issues": ["x"], "conditions_for_go": []}'
        parsed, error = parse_llm_json(
            raw,
            expected_keys=["reasoning", "blocking_issues", "conditions_for_go", "extra_field"],
            context="test",
        )
        assert error is None
        assert parsed["reasoning"] == "yes"
        assert parsed["extra_field"] == ""  # auto-filled

    def test_schema_full_mismatch(self):
        raw = '{"x": 1}'
        parsed, error = parse_llm_json(
            raw,
            expected_keys=["a", "b", "c", "d"],
            context="test",
        )
        assert error == "schema_mismatch"

    def test_nested_json_object(self):
        raw = '{"outer": {"inner": "value"}, "list": [1, 2]}'
        parsed, error = parse_llm_json(raw, context="test")
        assert error is None
        assert parsed["outer"]["inner"] == "value"

    def test_json_with_markdown_prose(self):
        raw = """
Sure! Here is the analysis:

```
{"classification": "new_regression", "confidence": 85}
```

That concludes my assessment.
"""
        parsed, error = parse_llm_json(raw, context="test")
        assert error is None
        assert parsed["classification"] == "new_regression"


# ============================================================================
# WS1: RegressionWatchman deterministic classification
# ============================================================================

from app.agents.regression_watchman import RegressionWatchman  # noqa: E402


class TestRegressionWatchmanClassification:
    def test_infra_category_as_string_detected(self):
        agent = RegressionWatchman()
        clusters = [{"cluster_id": "cl_1", "member_test_ids": ["t1", "t2"]}]
        analyses = {
            "t1": {"failure_category": "INFRASTRUCTURE", "is_flaky": False},
            "t2": {"failure_category": "INFRASTRUCTURE", "is_flaky": False},
        }
        history = {"cl_1": {"seen_in_baseline": False, "recent_occurrences": 0, "baseline_run_count": 5}}

        result = agent._deterministic_classify(clusters, analyses, history)

        assert result["cl_1"]["classification"] == "environmental_anomaly"
        assert result["cl_1"]["reason_code"] == "INFRA_FRACTION_HIGH"

    def test_infra_category_as_enum_detected(self):
        """Enum objects should be handled correctly (the original bug)."""
        from app.models.postgres import FailureCategory
        agent = RegressionWatchman()
        clusters = [{"cluster_id": "cl_1", "member_test_ids": ["t1"]}]
        analyses = {
            "t1": {"failure_category": FailureCategory.INFRASTRUCTURE, "is_flaky": False},
        }
        history = {"cl_1": {"seen_in_baseline": False, "recent_occurrences": 0, "baseline_run_count": 5}}

        result = agent._deterministic_classify(clusters, analyses, history)

        assert result["cl_1"]["classification"] == "environmental_anomaly"

    def test_empty_cluster_returns_low_confidence(self):
        agent = RegressionWatchman()
        clusters = [{"cluster_id": "cl_1", "member_test_ids": []}]
        result = agent._deterministic_classify(clusters, {}, {})
        assert result["cl_1"]["confidence"] == 20
        assert result["cl_1"]["reason_code"] == "EMPTY_CLUSTER"

    def test_missing_cluster_id_handled(self):
        agent = RegressionWatchman()
        clusters = [{"member_test_ids": []}]
        result = agent._deterministic_classify(clusters, {}, {})
        assert "" in result or "unknown" in result

    def test_unknown_category_does_not_crash(self):
        agent = RegressionWatchman()
        clusters = [{"cluster_id": "cl_1", "member_test_ids": ["t1"]}]
        analyses = {
            "t1": {"failure_category": "SOME_WEIRD_CATEGORY", "is_flaky": False},
        }
        history = {"cl_1": {"seen_in_baseline": False, "recent_occurrences": 0, "baseline_run_count": 5}}

        result = agent._deterministic_classify(clusters, analyses, history)
        assert "cl_1" in result

    def test_low_evidence_marked_uncertain(self):
        agent = RegressionWatchman()
        clusters = [{"cluster_id": "cl_1", "member_test_ids": ["t1"]}]
        analyses = {"t1": {"failure_category": "PRODUCT_BUG", "is_flaky": False}}
        history = {"cl_1": {"seen_in_baseline": False, "recent_occurrences": 0, "baseline_run_count": 1}}

        result = agent._deterministic_classify(clusters, analyses, history)

        assert result["cl_1"]["reason_code"] == "LOW_EVIDENCE"
        assert result["cl_1"]["confidence"] < 50

    def test_flaky_history_classification(self):
        agent = RegressionWatchman()
        clusters = [{"cluster_id": "cl_1", "member_test_ids": ["t1", "t2"]}]
        analyses = {
            "t1": {"failure_category": "FLAKY", "is_flaky": True},
            "t2": {"failure_category": "FLAKY", "is_flaky": True},
        }
        history = {"cl_1": {"seen_in_baseline": True, "recent_occurrences": 5, "baseline_run_count": 10}}

        result = agent._deterministic_classify(clusters, analyses, history)
        assert result["cl_1"]["classification"] == "known_flaky_recurrence"
        assert result["cl_1"]["reason_code"] == "FLAKY_HISTORY"


# ============================================================================
# WS4: Prompt redaction tests
# ============================================================================

from app.services.redaction_service import redact_text, redact_dict  # noqa: E402


class TestPromptRedaction:
    def test_bearer_token_redacted(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        result = redact_text(text)
        assert "eyJ" not in result
        assert "[REDACTED" in result

    def test_api_key_redacted(self):
        text = "api_key=sk_live_abcdef1234567890abcdef"
        result = redact_text(text)
        assert "sk_live" not in result
        assert "[REDACTED]" in result

    def test_password_redacted(self):
        text = "password=my_super_secret_pass123"
        result = redact_text(text)
        assert "my_super_secret" not in result

    def test_connection_string_redacted(self):
        text = "postgres://user:secretpassword@host:5432/db"
        result = redact_text(text)
        assert "secretpassword" not in result

    def test_jwt_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        result = redact_text(jwt)
        assert "eyJ" not in result

    def test_normal_text_preserved(self):
        text = "NullPointerException at com.example.LoginService.handle(LoginService.java:42)"
        result = redact_text(text)
        assert result == text

    def test_redact_dict_sensitive_keys(self):
        data = {
            "test_name": "test_login",
            "password": "secret123",
            "api_key": "key_abc",
            "error": "NullPointerException",
        }
        result = redact_dict(data)
        assert result["test_name"] == "test_login"
        assert result["password"] == "[REDACTED]"
        assert result["api_key"] == "[REDACTED]"
        assert result["error"] == "NullPointerException"

    def test_redact_empty_text(self):
        assert redact_text("") == ""
        assert redact_text(None) is None


# ============================================================================
# WS5: TestHealth signal source tests
# ============================================================================

from app.agents.test_health_agent import _looks_like_source_code, _analyze_source  # noqa: E402


class TestTestHealthSignal:
    def test_error_message_not_treated_as_code(self):
        """Plain error messages should NOT be scanned for anti-patterns."""
        error_msg = "java.lang.NullPointerException: Cannot invoke method on null object"
        assert not _looks_like_source_code(error_msg)

    def test_actual_code_detected(self):
        code = """
import pytest
class TestLogin:
    def test_login_success(self):
        time.sleep(5)
        assert True
"""
        assert _looks_like_source_code(code)

    def test_java_code_detected(self):
        code = "@Test\npublic void testLogin() {\n    Thread.sleep(5000);\n}"
        assert _looks_like_source_code(code)

    def test_short_text_not_code(self):
        assert not _looks_like_source_code("short")
        assert not _looks_like_source_code("")

    def test_analyze_source_finds_sleep(self):
        code = "def test_foo():\n    time.sleep(5)\n    assert True"
        violations = _analyze_source(code)
        assert any("sleep" in v["pattern"].lower() for v in violations)

    def test_analyze_source_finds_empty_catch(self):
        code = "try {\n    doSomething();\n} catch (Exception e) {}"
        violations = _analyze_source(code)
        assert any("catch" in v["pattern"].lower() for v in violations)


# ============================================================================
# WS5: Cluster schema validation tests
# ============================================================================

from app.agents.cluster_agent import ClusterAgent  # noqa: E402


class TestClusterValidation:
    def test_valid_clusters_preserved(self):
        agent = ClusterAgent()
        raw = [
            {"cluster_id": "cl_1", "member_test_ids": ["t1", "t2"], "label": "NPE", "representative_error": "err"},
        ]
        result = agent._validate_clusters(raw, {"t1", "t2", "t3"})
        assert len(result) == 1
        assert result[0]["member_test_ids"] == ["t1", "t2"]

    def test_invalid_member_ids_filtered(self):
        agent = ClusterAgent()
        raw = [
            {"cluster_id": "cl_1", "member_test_ids": ["t1", "invalid_id"], "label": "test"},
        ]
        result = agent._validate_clusters(raw, {"t1", "t2"})
        assert result[0]["member_test_ids"] == ["t1"]

    def test_duplicate_members_deduplicated(self):
        agent = ClusterAgent()
        raw = [
            {"cluster_id": "cl_1", "member_test_ids": ["t1", "t1", "t2"], "label": "test"},
        ]
        result = agent._validate_clusters(raw, {"t1", "t2"})
        assert result[0]["member_test_ids"] == ["t1", "t2"]

    def test_missing_cluster_id_auto_assigned(self):
        agent = ClusterAgent()
        raw = [
            {"member_test_ids": ["t1"], "label": "test"},
        ]
        result = agent._validate_clusters(raw, {"t1"})
        assert result[0]["cluster_id"].startswith("cl_auto_")

    def test_empty_cluster_after_validation_skipped(self):
        agent = ClusterAgent()
        raw = [
            {"cluster_id": "cl_1", "member_test_ids": ["invalid"], "label": "test"},
        ]
        result = agent._validate_clusters(raw, {"t1"})
        assert len(result) == 0

    def test_non_dict_cluster_skipped(self):
        agent = ClusterAgent()
        raw = [
            "not a dict",
            {"cluster_id": "cl_1", "member_test_ids": ["t1"], "label": "test"},
        ]
        result = agent._validate_clusters(raw, {"t1"})
        assert len(result) == 1

    def test_non_list_member_ids_handled(self):
        agent = ClusterAgent()
        raw = [
            {"cluster_id": "cl_1", "member_test_ids": "not_a_list", "label": "test"},
        ]
        result = agent._validate_clusters(raw, {"t1"})
        assert len(result) == 0  # Empty after validation

    def test_cross_cluster_deduplication(self):
        """Same test_id should not appear in multiple clusters."""
        agent = ClusterAgent()
        raw = [
            {"cluster_id": "cl_1", "member_test_ids": ["t1", "t2"], "label": "a"},
            {"cluster_id": "cl_2", "member_test_ids": ["t2", "t3"], "label": "b"},
        ]
        result = agent._validate_clusters(raw, {"t1", "t2", "t3"})
        all_members = [mid for c in result for mid in c["member_test_ids"]]
        assert len(all_members) == len(set(all_members))  # No duplicates

    def test_fallback_clusters_one_per_test(self):
        agent = ClusterAgent()
        result = agent._build_fallback_clusters(
            ["t1", "t2", "t3"],
            ["err1", "err2", "err3"],
        )
        assert len(result) == 3
        assert all(c["size"] == 1 for c in result)


# ============================================================================
# Patch-03: RegressionWatchman LLM timeout
# ============================================================================


class TestWatchmanLLMTimeout:
    @pytest.mark.asyncio
    async def test_llm_classify_returns_empty_on_timeout(self):
        """_llm_classify should return {} when LLM times out, not hang."""
        import asyncio
        from unittest.mock import MagicMock

        agent = RegressionWatchman()
        mock_llm = MagicMock()

        async def _slow_invoke(*args, **kwargs):
            await asyncio.sleep(9999)

        mock_llm.ainvoke = _slow_invoke

        import app.agents.regression_watchman as rwm
        original_get_llm = rwm.get_llm
        rwm.get_llm = lambda **kwargs: mock_llm
        rwm._LLM_CLASSIFY_TIMEOUT = 0.1  # Very short timeout for test

        try:
            result = await agent._llm_classify(
                uncertain={"cl_1": {"classification": "new_regression", "confidence": 40}},
                all_clusters=[{"cluster_id": "cl_1", "member_test_ids": ["t1"], "label": "test"}],
                history={"cl_1": {"seen_in_baseline": False}},
            )
            assert result == {}
        finally:
            rwm.get_llm = original_get_llm
            rwm._LLM_CLASSIFY_TIMEOUT = min(90, settings.AI_TIMEOUT_SECONDS)


# ============================================================================
# Patch-04: Redaction in outbound prompts
# ============================================================================



class TestRedactionInPrompts:
    def test_bearer_in_evidence_excerpt_redacted(self):
        """Evidence with secrets should be redacted before prompt assembly."""
        evidence = "Auth failed: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0ZXN0IjoxfQ.test_signature_here_abcdef"
        result = redact_text(evidence)
        assert "eyJ" not in result

    def test_api_key_in_config_redacted(self):
        config = "api_key=sk_live_abcdef1234567890abcdef"
        result = redact_text(config)
        assert "sk_live" not in result

    def test_password_in_error_message_redacted(self):
        error = "Connection failed: postgres://admin:supersecretpw123@db:5432/mydb"
        result = redact_text(error)
        assert "supersecretpw123" not in result

    def test_non_secret_technical_content_preserved(self):
        """Anti-pattern analysis, test names, etc. should not be redacted."""
        text = "NullPointerException at com.example.Service.handle(Service.java:42)\nBuild: 1234"
        assert redact_text(text) == text


# ============================================================================
# Patch-06: Branch-aware baseline
# ============================================================================


class TestBranchAwareBaseline:
    @pytest.mark.asyncio
    async def test_resolve_run_branch_returns_none_on_missing(self):
        """When branch lookup fails, should return None gracefully."""
        agent = RegressionWatchman()
        # _resolve_run_branch will fail since there's no DB, should return None
        result = await agent._resolve_run_branch("non-existent-id")
        assert result is None


# ============================================================================
# Patch-07: No raw status strings
# ============================================================================

import os  # noqa: E402


class TestNoRawStatusStrings:
    def test_no_raw_failed_broken_in_agents(self):
        """Ensure no raw 'FAILED'/'BROKEN' string literals in query filters of agent files."""
        agents_dir = os.path.join(
            os.path.dirname(__file__), "..", "app", "agents"
        )
        violations = []
        for fname in os.listdir(agents_dir):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(agents_dir, fname)
            with open(fpath, encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    # Only check .in_() query filter patterns
                    if '.in_([' in line and ('"FAILED"' in line or '"BROKEN"' in line):
                        violations.append(f"{fname}:{i}: {line.strip()}")
        assert not violations, "Raw status strings found:\n" + "\n".join(violations)


# ============================================================================
# Patch-08: Structlog usage
# ============================================================================


class TestStructlogMigration:
    def test_no_stdlib_logging_in_agents(self):
        """All agent files should use structlog, not stdlib logging.getLogger."""
        agents_dir = os.path.join(
            os.path.dirname(__file__), "..", "app", "agents"
        )
        violations = []
        for fname in os.listdir(agents_dir):
            if not fname.endswith(".py") or fname == "__init__.py":
                continue
            fpath = os.path.join(agents_dir, fname)
            with open(fpath, encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    if "logging.getLogger" in line and not line.strip().startswith("#"):
                        violations.append(f"{fname}:{i}: {line.strip()}")
        assert not violations, "stdlib logging.getLogger found:\n" + "\n".join(violations)


# ============================================================================
# Import verification (Patch-01 model change)
# ============================================================================

from app.models.postgres import Defect  # noqa: E402


class TestDefectModel:
    def test_defect_table_args_has_unique_index(self):
        """Defect model should have the partial unique index in __table_args__."""
        assert hasattr(Defect, "__table_args__")
        table_args = Defect.__table_args__
        index_names = [
            arg.name for arg in table_args
            if hasattr(arg, "name")
        ]
        assert "ix_defects_test_case_open_unique" in index_names
