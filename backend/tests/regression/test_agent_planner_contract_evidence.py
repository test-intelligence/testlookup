"""Guard for agent_planner's contract-evidence verifier check.

Reviewed in review/agent-planner (2026-06-02): clean (pure deterministic
planner/verifier — no DB/tenant/network/LLM). The existing
test_agent_planner suite covers all-green skipping, threshold-aware triage,
summary provenance, the mutating-action policy fail, and release-decision
trace — but NOT ``_check_contract_evidence_support``, which fails when a
non-empty agent output has no contract and warns when a contract exposes no
evidence_refs (unless it was a fallback / no_* / blocked decision). These pin
that check so the agent-auditability invariant can't silently regress.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from app.services.agent_planner import (  # noqa: E402
    build_workflow_plan,
    verify_workflow_execution,
)


def _plan_with_failures():
    # failures known + non-empty → not all-green, so analyses are expected.
    return build_workflow_plan(workflow_type="offline", failed_test_ids=["t1"], analyses={})


def _contract_check(result):
    return next(c for c in result["checks"] if c["name"] == "contract_evidence_support")


def test_fails_when_output_has_no_contract():
    state = {
        "completed_stages": ["ingestion", "summary", "root_cause_analysis"],
        "analyses": {"t1": {"confidence_score": 90}},  # output present
        "agent_contracts": {},                          # but no contract
    }
    result = verify_workflow_execution(_plan_with_failures(), state)
    check = _contract_check(result)
    assert check["status"] == "fail"
    assert "root_cause_analysis" in check["details"]["missing_contracts"]
    assert result["status"] == "failed"


def test_warns_when_contract_lacks_evidence_refs():
    state = {
        "completed_stages": ["ingestion", "summary", "root_cause_analysis"],
        "analyses": {"t1": {"confidence_score": 90}},
        "agent_contracts": {
            "root_cause_analysis": {
                "decision_reason": "analysed the failure",
                "fallback_used": False,
                "evidence_refs": [],
            }
        },
    }
    result = verify_workflow_execution(_plan_with_failures(), state)
    check = _contract_check(result)
    assert check["status"] == "warn"
    assert "root_cause_analysis" in check["details"]["missing_evidence_refs"]


def test_passes_when_no_evidence_but_fallback_used():
    state = {
        "completed_stages": ["ingestion", "summary", "root_cause_analysis"],
        "analyses": {"t1": {"confidence_score": 90}},
        "agent_contracts": {
            "root_cause_analysis": {
                "decision_reason": "llm_timeout",
                "fallback_used": True,   # no-evidence is acceptable for a fallback
                "evidence_refs": [],
            }
        },
    }
    result = verify_workflow_execution(_plan_with_failures(), state)
    assert _contract_check(result)["status"] == "pass"


def test_passes_when_evidence_refs_present():
    state = {
        "completed_stages": ["ingestion", "summary", "root_cause_analysis"],
        "analyses": {"t1": {"confidence_score": 90}},
        "agent_contracts": {
            "root_cause_analysis": {
                "decision_reason": "analysed",
                "fallback_used": False,
                "evidence_refs": ["splunk:123"],
            }
        },
    }
    result = verify_workflow_execution(_plan_with_failures(), state)
    assert _contract_check(result)["status"] == "pass"
