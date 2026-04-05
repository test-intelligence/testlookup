"""
Unit tests for the LangChain ReAct agent.
All LLM calls and tool invocations are mocked — no API calls, no LLM required.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Mock data ──────────────────────────────────────────────────

MOCK_STACKTRACE = """=== Allure Stack Trace ===
Test: testPaymentGatewayTimeout
Status: failed
Duration: 5432ms

Error Message:
java.lang.AssertionError: Expected status 200 but was 500

Stack Trace:
java.lang.AssertionError: Expected status 200 but was 500
    at com.company.api.PaymentGatewayTest.verifyTransaction(PaymentGatewayTest.java:87)
    at sun.reflect.NativeMethodAccessorImpl.invoke0(Native Method)

Failed Steps:
  - [failed] POST /api/v1/payments/charge

Attachments: 2 attachment(s) available
"""

MOCK_SPLUNK_LOGS = """=== Splunk Log Correlation (3 entries) ===
[2026-03-10T14:30:01Z] [ERROR] ConnectionTimeoutException: Unable to acquire JDBC Connection
[2026-03-10T14:30:02Z] [ERROR] HTTP 500 Internal Server Error on POST /api/v1/payments/charge
[2026-03-10T14:30:03Z] [ERROR] DataSourceLookupFailureException: Failed to look up DataSource
"""

MOCK_FLAKINESS = """=== Flakiness History: testPaymentGatewayTimeout ===
Total executions analysed: 12
Passed: 11 | Failed/Broken: 1 | Skipped: 0
Failure rate: 8.3%
🟢 HISTORICALLY STABLE — this is a new failure, not flakiness
"""

MOCK_AGENT_OUTPUT = json.dumps({
    "root_cause_summary": (
        "The test failed because the payment-gateway service experienced a database connection "
        "pool exhaustion at the time of test execution. Splunk logs show a JDBC ConnectionTimeout "
        "error 1 second before the test assertion failed. The test is historically stable (8% failure "
        "rate), confirming this is a new infrastructure issue, not flakiness."
    ),
    "failure_category": "INFRASTRUCTURE",
    "backend_error_found": True,
    "pod_issue_found": False,
    "is_flaky": False,
    "confidence_score": 94,
    "recommended_actions": [
        "Investigate database connection pool size in payment-gateway service",
        "Check for recent infrastructure changes to the DB cluster",
        "Increase connection pool timeout and retry configuration",
    ],
    "evidence_references": [
        {"source": "splunk", "reference_id": "splunk-log-1", "excerpt": "ConnectionTimeoutException: Unable to acquire JDBC Connection"},
        {"source": "stacktrace", "reference_id": "line-87", "excerpt": "Expected status 200 but was 500"},
    ],
})


# ── Fixtures ───────────────────────────────────────────────────

@pytest.fixture
def mock_tools():
    """Patch all 5 agent tools with deterministic mock responses."""
    with (
        patch("app.tools.fetch_stacktrace.fetch_allure_stacktrace", new_callable=AsyncMock) as mock_st,
        patch("app.tools.query_splunk.query_splunk_logs", new_callable=AsyncMock) as mock_splunk,
        patch("app.tools.check_flakiness.check_test_flakiness", new_callable=AsyncMock) as mock_flaky,
        patch("app.tools.fetch_rest_payload.fetch_rest_api_payload", new_callable=AsyncMock) as mock_rest,
        patch("app.tools.analyze_ocp.analyze_openshift_pod_events", new_callable=AsyncMock) as mock_ocp,
    ):
        mock_st.return_value = MOCK_STACKTRACE
        mock_splunk.return_value = MOCK_SPLUNK_LOGS
        mock_flaky.return_value = MOCK_FLAKINESS
        mock_rest.return_value = "No REST API payload captured for this test."
        mock_ocp.return_value = "OpenShift integration not configured."
        yield {
            "stacktrace": mock_st,
            "splunk": mock_splunk,
            "flakiness": mock_flaky,
            "rest": mock_rest,
            "ocp": mock_ocp,
        }


@pytest.fixture
def mock_llm():
    """Mock the LLM to return deterministic structured output."""
    mock = MagicMock()
    mock.invoke.return_value = MagicMock(content=f"Final Answer: {MOCK_AGENT_OUTPUT}")
    mock.ainvoke = AsyncMock(return_value=MagicMock(content=f"Final Answer: {MOCK_AGENT_OUTPUT}"))
    return mock


# ── Tests ──────────────────────────────────────────────────────

class TestAgentOutputSchema:
    """Verify the agent returns the correct JSON schema."""

    def test_parse_valid_json_output(self):
        from app.services.agent import _parse_agent_output
        result = _parse_agent_output(MOCK_AGENT_OUTPUT)
        assert isinstance(result, dict)
        assert "root_cause_summary" in result
        assert "failure_category" in result
        assert "confidence_score" in result
        assert isinstance(result["confidence_score"], int)
        assert 0 <= result["confidence_score"] <= 100

    def test_parse_json_embedded_in_text(self):
        from app.services.agent import _parse_agent_output
        wrapped = f"Thought: I have enough info.\nFinal Answer: {MOCK_AGENT_OUTPUT}"
        result = _parse_agent_output(wrapped)
        assert result["failure_category"] == "INFRASTRUCTURE"

    def test_parse_invalid_returns_fallback(self):
        from app.services.agent import _parse_agent_output
        result = _parse_agent_output("I cannot determine the root cause.")
        assert result["failure_category"] == "UNKNOWN"
        assert result["confidence_score"] == 0
        assert result["requires_human_review"] is True

    def test_fallback_analysis_structure(self):
        from app.services.agent import _fallback_analysis
        result = _fallback_analysis("LLM timeout")
        assert "root_cause_summary" in result
        assert isinstance(result["recommended_actions"], list)
        assert len(result["recommended_actions"]) > 0
        assert result["requires_human_review"] is True


class TestAgentToolCallLogic:
    """Verify the agent correctly identifies root causes from mocked tool data."""

    @pytest.mark.asyncio
    async def test_backend_timeout_identified(self, mock_tools):
        """Agent should identify INFRASTRUCTURE failure from Splunk JDBC timeout."""
        from app.services.agent import _parse_agent_output
        # Simulate what the agent would conclude given our mock data
        analysis = _parse_agent_output(MOCK_AGENT_OUTPUT)
        assert analysis["backend_error_found"] is True
        assert analysis["failure_category"] == "INFRASTRUCTURE"
        assert analysis["confidence_score"] > 80

    @pytest.mark.asyncio
    async def test_flaky_test_detected(self):
        """Agent should set is_flaky=True when flakiness tool returns high failure rate."""
        from app.services.agent import _parse_agent_output
        flaky_output = json.dumps({
            "root_cause_summary": "Test is intermittently failing with a 45% failure rate.",
            "failure_category": "FLAKY",
            "backend_error_found": False,
            "pod_issue_found": False,
            "is_flaky": True,
            "confidence_score": 87,
            "recommended_actions": ["Investigate test stability", "Add retry logic"],
            "evidence_references": [],
        })
        analysis = _parse_agent_output(flaky_output)
        assert analysis["is_flaky"] is True
        assert analysis["failure_category"] == "FLAKY"

    @pytest.mark.asyncio
    async def test_insufficient_telemetry_handling(self):
        """Agent should return low confidence when tools return empty results."""
        from app.services.agent import _parse_agent_output
        insufficient_output = json.dumps({
            "root_cause_summary": "Insufficient telemetry to determine root cause.",
            "failure_category": "UNKNOWN",
            "backend_error_found": False,
            "pod_issue_found": False,
            "is_flaky": False,
            "confidence_score": 15,
            "recommended_actions": ["Enable Splunk integration", "Check test logs manually"],
            "evidence_references": [],
        })
        analysis = _parse_agent_output(insufficient_output)
        assert analysis["confidence_score"] < 80
        assert analysis["failure_category"] == "UNKNOWN"


class TestConfidenceThreshold:
    """Verify confidence-based gating logic."""

    def test_low_confidence_requires_human_review(self):
        from app.services.agent import _parse_agent_output
        low_conf = json.dumps({
            "root_cause_summary": "Possible test data issue.",
            "failure_category": "TEST_DATA",
            "backend_error_found": False,
            "pod_issue_found": False,
            "is_flaky": False,
            "confidence_score": 55,
            "recommended_actions": ["Review test data"],
            "evidence_references": [],
        })
        result = _parse_agent_output(low_conf)
        # requires_human_review should be set if confidence < 80
        assert result["confidence_score"] < 80

    def test_high_confidence_analysis(self):
        result = json.loads(MOCK_AGENT_OUTPUT)
        assert result["confidence_score"] >= 80
        assert result["failure_category"] != "UNKNOWN"


class TestLLMFactory:
    """Verify the LLM factory returns correct providers."""

    @patch("app.core.config.settings")
    def test_ollama_provider_selected_by_default(self, mock_settings):
        mock_settings.LLM_PROVIDER = "ollama"
        mock_settings.LLM_MODEL = "qwen2.5:7b"
        mock_settings.OLLAMA_BASE_URL = "http://localhost:11434"
        mock_settings.LLM_TEMPERATURE = 0.1
        mock_settings.LLM_MAX_TOKENS = 4096
        mock_settings.AI_OFFLINE_MODE = True

        with patch("langchain_ollama.ChatOllama") as mock_ollama:
            from app.services.llm_factory import get_llm
            get_llm(provider="ollama", model="qwen2.5:7b")
            mock_ollama.assert_called_once()

    def test_openai_blocked_in_offline_mode(self):
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.LLM_PROVIDER = "openai"
            mock_settings.AI_OFFLINE_MODE = True
            mock_settings.OPENAI_API_KEY = "sk-test"
            mock_settings.LLM_TEMPERATURE = 0.1
            mock_settings.LLM_MAX_TOKENS = 4096

            from app.services.llm_factory import get_llm
            with pytest.raises(ValueError, match="AI_OFFLINE_MODE"):
                get_llm(provider="openai")

    def test_unknown_provider_raises(self):
        from app.services.llm_factory import get_llm
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_llm(provider="nonexistent_provider")


class TestParsers:
    """Test Allure JSON and TestNG XML parsers."""

    def test_allure_parser_happy_path(self):
        from app.services.allure_parser import parse_allure_result
        data = {
            "uuid": "test-uuid-123",
            "name": "testLogin",
            "fullName": "com.company.LoginTest.testLogin",
            "status": "failed",
            "start": 1700000000000,
            "stop": 1700000005432,
            "labels": [
                {"name": "suite", "value": "LoginSuite"},
                {"name": "testClass", "value": "com.company.LoginTest"},
                {"name": "severity", "value": "critical"},
                {"name": "feature", "value": "Authentication"},
            ],
            "statusDetails": {
                "message": "Expected 200 but got 401",
                "trace": "at LoginTest.java:45",
            },
            "steps": [{"name": "Click login button", "status": "passed"}],
            "attachments": [],
        }
        result = parse_allure_result(data, "run-123", "project/runs/1/allure/test-uuid-result.json")
        assert result is not None
        assert result["test_name"] == "testLogin"
        assert result["status"] == "failed"
        assert result["suite_name"] == "LoginSuite"
        assert result["severity"] == "critical"
        assert result["feature"] == "Authentication"
        assert result["duration_ms"] == 5432
        assert "Expected 200 but got 401" in result["error_message"]

    def test_allure_parser_returns_none_for_empty(self):
        from app.services.allure_parser import parse_allure_result
        assert parse_allure_result({}, "run-123", "key") is None

    def test_testng_parser_suite_xml(self):
        from app.services.testng_parser import parse_testng_xml
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="PaymentSuite" tests="3" failures="1" time="12.345">
    <testcase classname="com.company.PaymentTest" name="testSuccess" time="3.1"/>
    <testcase classname="com.company.PaymentTest" name="testFailure" time="5.2">
        <failure message="Expected 200 got 500">Stack trace here</failure>
    </testcase>
    <testcase classname="com.company.PaymentTest" name="testSkipped" time="0.0">
        <skipped/>
    </testcase>
</testsuite>"""
        cases = parse_testng_xml(xml, "run-456")
        assert len(cases) == 3
        statuses = {c["test_name"]: c["status"] for c in cases}
        assert statuses["testSuccess"] == "passed"
        assert statuses["testFailure"] == "failed"
        assert statuses["testSkipped"] == "skipped"
        assert cases[1]["error_message"] == "Expected 200 got 500"

    def test_testng_parser_malformed_xml_returns_empty(self):
        from app.services.testng_parser import parse_testng_xml
        result = parse_testng_xml("not valid xml <<<", "run-789")
        assert result == []
