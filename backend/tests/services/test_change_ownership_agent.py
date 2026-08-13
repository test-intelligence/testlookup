from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.agents.change_ownership_agent import _public_diff
from app.agents.decision_report_agent import build_decision_intelligence
from app.agents.workflow import change_ownership_node
from app.services.agent_planner import build_workflow_plan


def test_change_ownership_planner_is_default_off_and_hashed():
    disabled = build_workflow_plan(workflow_type="deep", failed_test_ids=["tc-1"])
    disabled_stage = next(item for item in disabled["stages"] if item["stage"] == "change_ownership")
    assert disabled_stage["planned"] is False

    enabled = build_workflow_plan(
        workflow_type="deep",
        failed_test_ids=["tc-1"],
        change_ownership_enabled=True,
    )
    enabled_stage = next(item for item in enabled["stages"] if item["stage"] == "change_ownership")
    assert enabled_stage["planned"] is True
    assert enabled["plan_sha256"]


def test_change_ownership_enabled_node_emits_typed_contract(monkeypatch):
    class FakeAgent:
        async def run(self, state):
            return {"status": "complete", "baseline_diff": {"baseline_available": True}, "ownership_resolutions": [], "summary": "ok"}
    import app.agents.workflow as workflow
    monkeypatch.setattr(workflow, "_change_ownership", FakeAgent())
    result = asyncio.run(workflow.change_ownership_node({"change_ownership_enabled": True}))
    assert result["change_ownership_findings"]["status"] == "complete"
    assert result["agent_contracts"]["change_ownership"]["evidence_refs"]


def test_change_ownership_skip_node_is_deterministic():
    result = asyncio.run(change_ownership_node({"change_ownership_enabled": False}))
    assert result["change_ownership_findings"]["status"] == "not_enough_evidence"
    assert result["skipped_stages"] == ["change_ownership"]


def test_change_ownership_public_projection_redacts_and_bounds():
    projected = _public_diff({
        "baseline_available": True,
        "baseline_run_id": "run-1",
        "baseline_build_number": "build-1",
        "build_number": "build-2",
        "pass_rate": 90,
        "baseline_pass_rate": 99,
        "pass_rate_delta": -9,
        "new_failing_count": 2,
        "resolved_count": 1,
        "commit_count": 3,
        "regression_classification": "product_bug",
    })
    assert projected["pass_rate_delta"] == -9
    assert projected["classification"] == "product_bug"


def test_change_ownership_is_canonical_decision_evidence():
    state = {
        "failed_test_ids": ["tc-1"],
        "failure_clusters": [],
        "deep_findings": {},
        "flaky_findings": [],
        "test_health_findings": [],
        "contract_findings": None,
        "log_findings": None,
        "regression_classification": None,
        "change_ownership_enabled": True,
        "change_ownership_findings": {
            "status": "complete",
            "baseline_diff": {"baseline_available": True, "pass_rate_delta": -4},
            "ownership_resolutions": [{"cluster_id": "c1", "team_name": "payments"}],
        },
        "cluster_investigation_results": None,
        "release_decision": {"recommendation": "NO_GO", "risk_score": 90},
        "structured_summary": None,
        "stage_errors": {},
        "total_tests": 1,
        "pass_rate": 0.0,
        "build_number": "b1",
        "test_run_id": "r1",
        "project_id": "p1",
        "pipeline_run_id": "pipeline-1",
        "completed_stages": [],
        "skipped_stages": [],
    }
    decision = build_decision_intelligence(state)
    assert decision["change_ownership_findings"]["baseline_diff"]["pass_rate_delta"] == -4
    assert "change_ownership" in decision["source_stages"]
    assert decision["evidence_bundle_sha256"]
class _Result:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, results):
        self.results = list(results)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, _statement):
        return self.results.pop(0)


def test_change_ownership_re_resolves_run_and_cluster_members(monkeypatch):
    import app.agents.change_ownership_agent as module

    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    member_id = uuid.uuid4()
    run = SimpleNamespace(id=run_id, project_id=project_id)
    cluster = SimpleNamespace(cluster_id="c1", size=1, member_test_ids=[str(member_id)])
    session = _Session([
        _Result(scalar=run),
        _Result(rows=[cluster]),
        _Result(rows=[member_id]),
    ])
    monkeypatch.setattr(module, "AsyncSessionLocal", lambda: session)
    async def fake_diff(_run, _db):
        return {"baseline_available": True, "baseline_run_id": "baseline", "pass_rate_delta": -2}
    async def fake_owner(_db, _project, _members):
        return SimpleNamespace(to_dict=lambda: {"team_name": "payments", "confidence": "high"})
    monkeypatch.setattr(module, "compute_regression_diff", fake_diff)
    monkeypatch.setattr(module, "resolve_cluster_ownership", fake_owner)

    result = asyncio.run(module.ChangeOwnershipAgent().run({"project_id": str(project_id), "test_run_id": str(run_id)}))
    assert result["status"] == "complete"
    assert result["ownership_resolutions"][0]["team_name"] == "payments"


def test_change_ownership_fails_closed_on_stale_cluster_membership(monkeypatch):
    import app.agents.change_ownership_agent as module

    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    member_id = uuid.uuid4()
    session = _Session([
        _Result(scalar=SimpleNamespace(id=run_id, project_id=project_id)),
        _Result(rows=[SimpleNamespace(cluster_id="c1", size=1, member_test_ids=[str(member_id)])]),
        _Result(rows=[]),
    ])
    monkeypatch.setattr(module, "AsyncSessionLocal", lambda: session)
    async def no_diff(*_):
        return {}
    monkeypatch.setattr(module, "compute_regression_diff", no_diff)
    result = asyncio.run(module.ChangeOwnershipAgent().run({"project_id": str(project_id), "test_run_id": str(run_id)}))
    assert result["status"] == "failed"
    assert result["summary"] == "cluster_member_authority_invalid"