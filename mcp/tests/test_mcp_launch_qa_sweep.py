"""Defects found by the API/MCP sweep that followed the launch QA handoff (TL-QA-11 onward).

Each case drives the registered tool, resource or prompt through the real MCP
HTTP client against a synthetic REST peer whose bodies have the shape the
backend sends: the null figures, the totals and the field names are the ones
the backend's services and response models produce.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

MCP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCP_DIR))

import client  # noqa: E402
import server  # noqa: E402

PROJECT = "11111111-1111-4111-8111-111111111111"
RUN = "22222222-2222-4222-8222-222222222222"
RELEASE = "66666666-6666-4666-8666-666666666666"


@pytest.fixture
def mcp_request(monkeypatch):
    monkeypatch.setattr(client, "_request_access_token", lambda: "qa-caller")

    def call(handler, name, arguments=None):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://backend") as http:
                monkeypatch.setattr(client, "_client", http)
                if arguments is None:
                    content, = await server.mcp.read_resource(name)
                    document = json.loads(content.content)
                    return document.get("data", document)
                result = await server.mcp.call_tool(name, arguments)
                if result.structured_content is not None and not any(b.type == "text" for b in result.content):
                    return result.structured_content
                text = "\n".join(block.text for block in result.content if block.type == "text")
                assert not result.is_error, text
                return text

        return asyncio.run(run())

    return call


def _answer(body, status=200):
    return lambda request: httpx.Response(status, json=body)


def _structured(result):
    """A dict-returning tool's result, whichever way the SDK carried it."""
    return result if isinstance(result, dict) else json.loads(result)


# ── TL-QA-11: AI triage summary with nothing analysed ────────────────────────


def test_ai_summary_renders_when_nothing_was_analysed(mcp_request):
    body = {"total_analysed": 0, "high_confidence": 0, "needs_review": 0, "avg_confidence": None,
            "flaky_detected": 0, "backend_errors": 0, "pod_issues": 0}

    text = mcp_request(_answer(body), "get_ai_analysis_summary", {"project_id": PROJECT})

    assert "| Avg Confidence Score | **n/a** |" in text
    assert "No AI analyses recorded yet" in text


def test_ai_summary_keeps_a_measured_average(mcp_request):
    body = {"total_analysed": 4, "high_confidence": 3, "needs_review": 1, "avg_confidence": 81.5}

    text = mcp_request(_answer(body), "get_ai_analysis_summary", {"project_id": PROJECT})

    assert "**82%**" in text and "3 (75%)" in text


# ── TL-QA-12: coverage report with unmeasured rates ──────────────────────────


def test_coverage_report_renders_an_empty_window(mcp_request):
    body = {"summary": {"unique_tests": 0, "suite_count": 0, "total_executions": 0, "avg_pass_rate": None,
                        "days_with_runs": 0}, "suites": []}

    text = mcp_request(_answer(body), "get_coverage_report", {"project_id": PROJECT})

    assert "- **Avg Pass Rate:** not measured" in text


def test_coverage_report_lists_a_skipped_only_suite_last_and_says_when_the_list_is_cut(mcp_request):
    suites = [
        {"suite_name": "skipped-only", "unique_tests": 3, "passed": 0, "failed": 0, "skipped": 3, "pass_rate": None},
        {"suite_name": "healthy", "unique_tests": 9, "passed": 9, "failed": 0, "skipped": 0, "pass_rate": 100.0},
        {"suite_name": "failing", "unique_tests": 4, "passed": 0, "failed": 4, "skipped": 0, "pass_rate": 0.0},
    ]
    body = {"summary": {"unique_tests": 16, "suite_count": 80, "total_executions": 16, "avg_pass_rate": 69.2,
                        "days_with_runs": 3}, "suites": suites}

    text = mcp_request(_answer(body), "get_coverage_report", {"project_id": PROJECT})
    rows = [line for line in text.splitlines() if line.startswith("| ") and "Suite" not in line]

    names = [row.split("|")[1].strip() for row in rows]
    assert names == [f"<test-output>{name}</test-output>" for name in ("failing", "healthy", "skipped-only")]
    assert rows[0].rstrip().endswith("0.0% |") and rows[2].rstrip().endswith("| not measured |")
    assert "3 of 80 suites shown" in text


# ── TL-QA-13: flaky leaderboard counts, manual flags and fingerprints ────────


def test_flaky_tests_states_the_total_marks_manual_rows_and_shows_fingerprints(mcp_request):
    body = {"total": 30, "items": [
        {"test_name": "test_pay", "suite_name": "checkout", "test_fingerprint": "b94c0127f321daea",
         "failure_rate_pct": 40.0, "total_runs": 20, "fail_count": 8, "last_seen": "2026-11-20T10:00:00Z"},
        {"test_name": "test_login", "suite_name": "auth", "test_fingerprint": "0a1b2c3d4e5f6071",
         "failure_rate_pct": 100.0, "total_runs": 1, "fail_count": 1, "source": "manual"},
    ]}

    text = mcp_request(_answer(body), "get_flaky_tests", {"project_id": PROJECT, "limit": 2})
    measured, manual = (next(line for line in text.splitlines() if name in line) for name in ("test_pay", "test_login"))

    assert "(showing 2 of 30)" in text and "28 more not shown" in text
    assert "`b94c0127f321daea`" in measured and "40%" in measured
    assert "`0a1b2c3d4e5f6071`" in manual and "flagged by a person" in manual and "100%" not in manual


def test_flaky_tests_counts_a_complete_page_plainly(mcp_request):
    body = {"total": 1, "items": [{"test_name": "test_pay", "test_fingerprint": "b94c0127f321daea",
                                   "failure_rate_pct": 40.0}]}

    text = mcp_request(_answer(body), "get_flaky_tests", {"project_id": PROJECT})

    assert "(1 found)" in text and "more not shown" not in text and "flagged by a person" not in text


# ── TL-QA-14: test case list has no error column to leave blank ──────────────


def test_test_case_list_points_to_the_detail_read_for_errors(mcp_request):
    body = {"items": [{"id": RUN, "test_name": "test_pay", "suite_name": "checkout", "status": "FAILED",
                       "duration_ms": 1200, "failure_category": "PRODUCT_BUG"}], "total": 1, "pages": 1}

    text = mcp_request(_answer(body), "list_test_cases", {"run_id": RUN})
    header = next(line for line in text.splitlines() if line.startswith("| Test Name"))

    assert "Error" not in header
    assert "PRODUCT_BUG |" in text and "| - |" not in text
    assert "use `get_test_case`" in text


# ── TL-QA-15: ownership rules read the fields the API sends ──────────────────


def test_ownership_rules_show_pattern_team_and_service(mcp_request):
    rules = [
        {"id": "1", "match_type": "suite_name", "match_pattern": "checkout-*", "service_name": "payments-api",
         "team_name": "Payments", "team_contact": "#payments", "priority": 10, "is_active": True},
        {"id": "2", "match_type": "path", "match_pattern": "tests/legacy/**", "service_name": "monolith",
         "team_name": "Platform", "team_contact": None, "priority": 1, "is_active": False},
    ]

    text = mcp_request(_answer(rules), "list_ownership_rules", {"project_id": PROJECT})

    assert "?" not in text
    assert "priority 10 · suite_name `checkout-*` → team **Payments**" in text
    assert "Service: payments-api" in text and "Contact: #payments" in text
    assert "path `tests/legacy/**` → team **Platform** (inactive)" in text


# ── TL-QA-16: "open" defects include the ones in progress ────────────────────


def test_open_defects_resource_asks_for_every_live_defect(mcp_request):
    seen = {}

    def backend(request):
        seen.update(request.url.params)
        return httpx.Response(200, json={"items": [], "total": 0})

    mcp_request(backend, f"testlookup://projects/{PROJECT}/defects/open")

    assert seen["resolution_status"] == "ACTIVE"


@pytest.mark.parametrize("prompt", ["release_readiness_report", "defect_triage_session"])
def test_recipes_ask_for_active_defects_not_only_open_ones(prompt):
    result = asyncio.run(server.mcp.get_prompt(prompt, {"project_id": PROJECT}))
    text = "\n".join(m.content.text for m in result.messages if m.content.type == "text")
    step = next(line for line in text.splitlines() if "`get_defects`" in line)

    assert 'resolution_status="ACTIVE"' in step and 'resolution_status="OPEN"' not in text


def test_defect_tools_document_the_statuses_the_api_has():
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    described = tools["get_defects"].description

    assert all(word in described for word in ("OPEN", "IN_PROGRESS", "RESOLVED", "INVALID", "ACTIVE"))
    assert "WONTFIX" not in described and "DUPLICATE" not in described


def test_an_in_progress_defect_gets_its_own_icon(mcp_request):
    body = {"items": [{"jira_ticket_id": "PAY-1", "test_name": "test_pay", "resolution_status": "IN_PROGRESS",
                       "failure_category": "PRODUCT_BUG"}], "total": 1, "pages": 1}

    text = mcp_request(_answer(body), "get_defects", {"project_id": PROJECT, "resolution_status": "ACTIVE"})

    assert "🟡 IN_PROGRESS" in text


# ── TL-QA-17 / TL-QA-18: project resources ───────────────────────────────────


def test_metrics_resource_explains_a_body_with_no_kpis(mcp_request):
    data = mcp_request(_answer({"meta": {"scope": {}, "total": 0}}), f"testlookup://projects/{PROJECT}/metrics")

    assert data["metrics"] is None and data["project_id"] == PROJECT
    assert "no access to it, or no data" in data["note"]


def test_metrics_resource_still_returns_kpis_without_the_envelope(mcp_request):
    body = {"meta": {"scope": {}}, "avg_pass_rate_7d": {"value": 91.0}, "release_readiness": "GREEN"}

    data = mcp_request(_answer(body), f"testlookup://projects/{PROJECT}/metrics")

    assert data == {"avg_pass_rate_7d": {"value": 91.0}, "release_readiness": "GREEN"}


def test_latest_runs_resource_is_not_limited_to_the_last_thirty_days(mcp_request):
    seen = {}

    def backend(request):
        seen.update(request.url.params)
        return httpx.Response(200, json={"items": [], "total": 0})

    mcp_request(backend, f"testlookup://projects/{PROJECT}/runs/latest")

    assert seen["days"] == "0" and seen["order"] == "newest"


# ── TL-QA-19: one line per failure category ──────────────────────────────────


def test_failure_categories_are_summed_across_kinds(mcp_request):
    body = {"items": [
        {"category": "UNKNOWN", "count": 9, "kind": "ai"},
        {"category": "UNKNOWN", "count": 2, "kind": "rule"},
        {"category": "PRODUCT_BUG", "count": 11, "kind": "ai"},
    ]}

    text = mcp_request(_answer(body), "get_failure_categories", {"project_id": PROJECT})
    rows = [line for line in text.splitlines() if "**: " in line]

    assert len(rows) == 2
    assert "**PRODUCT_BUG**: 11 (50.0%)" in rows[0] and "**UNKNOWN**: 11 (50.0%)" in rows[1]
    assert "**Total failures categorised:** 22" in text


# ── TL-QA-22: transport failures say what happened ───────────────────────────


@pytest.mark.parametrize(
    ("error", "code"),
    [(httpx.ReadTimeout(""), "timeout"), (httpx.ConnectTimeout(""), "timeout"), (httpx.ConnectError(""), "unreachable")],
    ids=["read-timeout", "connect-timeout", "refused"],
)
def test_a_transport_failure_is_never_an_empty_error(error, code):
    payload = client.error_payload(error)

    assert payload["ok"] is False and payload["code"] == code
    assert payload["error"].strip() and type(error).__name__ in payload["error"]
    if code == "timeout":
        assert "may or may not have been applied" in payload["detail"]


def test_an_http_refusal_keeps_its_status_and_detail():
    response = httpx.Response(409, json={"detail": "Cannot approve from status QUARANTINED"},
                              request=httpx.Request("POST", "http://backend/x"))

    payload = client.error_payload(httpx.HTTPStatusError("409", request=response.request, response=response))

    assert payload["status_code"] == 409 and payload["detail"] == "Cannot approve from status QUARANTINED"
    assert "code" not in payload


# ── TL-QA-23: a login that needs a second factor ─────────────────────────────


@pytest.mark.parametrize(
    ("body", "said"),
    [
        ({"mfa_required": True, "mfa_token": "challenge"}, "multi-factor authentication"),
        ({"mfa_enrollment_required": True, "enrollment_token": "enrol"}, "has not set it up"),
        ({}, "no access token"),
    ],
    ids=["challenge", "enrolment", "no-token"],
)
def test_login_explains_a_sign_in_that_returns_no_token(monkeypatch, body, said):
    monkeypatch.setattr(client, "_request_access_token", lambda: None)
    monkeypatch.setattr(client, "_stdio_access_token", "stale")

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
            monkeypatch.setattr(client, "_client", http)
            result = await server.mcp.call_tool("login", {"username": "qa", "password": "not-a-real-one"})
            return result.is_error, "\n".join(b.text for b in result.content if b.type == "text")

    is_error, text = asyncio.run(run())

    assert not is_error and text.startswith("Login not completed:") and said in text
    assert client._stdio_access_token is None
    assert "challenge" not in text and "enrol" not in text.replace("Enrol", "")


def test_a_normal_login_answer_still_yields_its_token():
    assert client.access_token_from_login({"access_token": "abc", "token_type": "bearer"}) == "abc"


# ── TL-QA-24: a release that does not exist is not "never evaluated" ─────────


@pytest.mark.parametrize(
    ("detail", "evaluated_answer"),
    [("This release has not been evaluated yet", True), ("Release not found", False)],
    ids=["never-evaluated", "no-such-release"],
)
def test_release_gate_tells_a_missing_release_from_an_unevaluated_one(mcp_request, detail, evaluated_answer):
    result = _structured(mcp_request(_answer({"detail": detail}, status=404), "get_release_gate", {"release": RELEASE}))

    if evaluated_answer:
        assert result["ok"] is True and result["evaluated"] is False and result["release_id"] == RELEASE
    else:
        assert result["ok"] is False and result["status_code"] == 404 and result["detail"] == "Release not found"
        assert "evaluated" not in result
