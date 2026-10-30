from types import SimpleNamespace

import pytest

from app.services.agent import _tool_observation_references
from app.tools.fetch_stacktrace import fetch_allure_stacktrace
from app.tools.fetch_rest_payload import fetch_rest_api_payload
from app.tools.investigation_context import (
    InvestigationContext,
    reset_investigation_context,
    set_investigation_context,
)


@pytest.mark.asyncio
async def test_model_cannot_change_bound_test_identity(monkeypatch):
    monkeypatch.setattr(
        "app.tools.fetch_stacktrace.get_mongo_db",
        lambda: pytest.fail("foreign scope must be rejected before database access"),
    )
    token = set_investigation_context(InvestigationContext(
        project_id="project-a", run_id="run-a", test_case_id="test-a",
        test_name="checkout",
    ))
    try:
        result = await fetch_allure_stacktrace.ainvoke({"test_case_id": "test-b"})
    finally:
        reset_investigation_context(token)
    assert "denied" in result.lower()


def test_citations_come_from_executed_observations_not_model_json():
    action = SimpleNamespace(tool="fetch_allure_stacktrace")
    references = _tool_observation_references([
        (action, "token=short-secret assertion failed"),
    ])
    assert references == [{
        "source": "fetch_allure_stacktrace",
        "kind": "tool_observation",
        "excerpt": "token=[REDACTED] assertion failed",
        "freshness": "current_run",
        "sensitivity": "restricted",
    }]


def test_denied_or_unavailable_tool_results_are_evidence_gaps_not_citations():
    action = SimpleNamespace(tool="fetch_allure_stacktrace")
    assert _tool_observation_references([
        (action, "Stack trace lookup denied: requested test is outside scope."),
        (action, "Stack trace lookup unavailable: no authorized investigation context."),
    ]) == []


@pytest.mark.asyncio
async def test_rest_payload_removes_credential_headers_and_redacts_bodies(monkeypatch):
    # W23d (deliberate): the tool has ONE rendering — the store's v1 doc, every body inside W23c's
    # <<data>> block. The pre-W23d unwrapped branch (and its case-id-only fallback) is gone.
    doc = {
        "v": 1, "project_id": "project-a", "test_run_id": "run-a", "test_case_id": "test-a", "seq": 0,
        "method": "POST", "endpoint": "/pay", "status": 500,
        "request": {
            "headers": {
                "Cookie": "sessionid=super-secret-session-value",
                "Authorization": "Bearer abcdefghijklmnopqrstuvwxyz",
                "Content-Type": "application/json",
            },
            "body": "token=short-secret",
        },
        "response": {"body": "Set-Cookie: sid=another-secret-value"},
    }

    class _Cursor:
        def sort(self, *_a): return self
        def limit(self, *_a): return self
        async def to_list(self, *_a): return [doc]

    class _Collection:
        async def find_one(self, query):
            return doc if query.get("test_run_id") == "run-a" else None

        def find(self, *_a): return _Cursor()

        async def count_documents(self, _query): return 1

    class _Mongo:
        def __getitem__(self, _name): return _Collection()

    monkeypatch.setattr(
        "app.tools.fetch_rest_payload.get_mongo_db", lambda: _Mongo()
    )
    token = set_investigation_context(InvestigationContext(
        project_id="project-a", run_id="run-a", test_case_id="test-a",
        test_name="checkout",
    ))
    try:
        result = await fetch_rest_api_payload.ainvoke({"test_case_id": "test-a"})
    finally:
        reset_investigation_context(token)

    assert "super-secret-session-value" not in result
    assert "another-secret-value" not in result
    assert "short-secret" not in result
    assert "Authorization" not in result
    assert "Cookie: sessionid=" not in result
    assert "<<data>>" in result and "[REDACTED]" in result
    assert "Content-Type: application/json" in result
