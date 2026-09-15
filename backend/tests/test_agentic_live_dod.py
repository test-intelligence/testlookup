"""T22: the repeatable live probe fails closed on agent API drift."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_agentic_live_dod.py"
SPEC = importlib.util.spec_from_file_location("verify_agentic_live_dod", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
live_dod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live_dod)


def _entry(*, agent_id: str, invokable: bool, sync: bool, report: bool):
    return {
        "agent_id": agent_id,
        "invokable": invokable,
        "sync_eligible": sync,
        "produces_report": report,
    }


def test_generated_collection_keeps_the_live_invoke_request_contract():
    live_dod.verify_postman_contract(ROOT / "architecture/api/agents.postman_collection.json")


def test_live_targets_exclude_workflow_only_sync_capabilities():
    catalog = [
        _entry(agent_id="agent.internal.v1", invokable=False, sync=True, report=False),
        _entry(agent_id="agent.fast.v1", invokable=True, sync=True, report=False),
        _entry(agent_id="agent.report.v1", invokable=True, sync=False, report=True),
        _entry(agent_id="agent.slow.v1", invokable=True, sync=False, report=False),
    ]

    assert [entry["agent_id"] for entry in live_dod.select_targets(catalog)] == [
        "agent.fast.v1",
        "agent.report.v1",
    ]


@pytest.mark.parametrize(
    ("entry", "status", "review_state"),
    [
        (_entry(agent_id="agent.fast.v1", invokable=True, sync=True, report=False), "passed", "not_applicable"),
        (_entry(agent_id="agent.report.v1", invokable=True, sync=False, report=True), "completed", "pending_review"),
    ],
)
def test_terminal_contract_accepts_only_the_expected_public_state(entry, status, review_state):
    live_dod.verify_invocation_result(
        entry,
        initial_http=202,
        payload={"status": status, "review": {"state": review_state}},
    )


@pytest.mark.parametrize(
    ("initial_http", "status", "review_state"),
    [
        (200, "passed", "not_applicable"),
        (202, "running", "not_applicable"),
        (202, "completed", "not_applicable"),
        (202, "passed", "pending_review"),
    ],
)
def test_sync_terminal_contract_rejects_every_wrong_live_result(initial_http, status, review_state):
    entry = _entry(agent_id="agent.fast.v1", invokable=True, sync=True, report=False)
    with pytest.raises(live_dod.VerificationError):
        live_dod.verify_invocation_result(
            entry,
            initial_http=initial_http,
            payload={"status": status, "review": {"state": review_state}},
        )
