from app.services.decision_report_supersession_service import (
    merge_terminal_child_projection,
    validate_supersession_projection,
)
from app.agents.decision_report_agent import compute_decision_evidence_hash
import pytest


def _decision():
    return {
        "status": "complete",
        "metrics": {"total_tests": 2, "failed_tests": 1},
        "quality_review": {
            "missing_or_failed_specialists": [],
            "requires_human_review": False,
        },
        "source_stages": ["release_risk"],
        "verification": {"status": "passed", "checks": ["baseline"]},
        "evidence_bundle_sha256": "old",
    }


def test_merge_is_bounded_deterministic_and_does_not_alias_source():
    source = _decision()
    children = [
        {"status": "completed", "failure_cluster_id": "a", "verdict": {"x": 1}},
        {"status": "cancelled", "failure_cluster_id": "b", "stop_reason": "timeout"},
    ]
    merged = merge_terminal_child_projection(source, children)

    assert merged["status"] == "degraded"
    assert merged["cluster_investigation_results"]["completed_count"] == 1
    assert merged["quality_review"]["missing_or_failed_specialists"] == [
        "cluster_investigation"
    ]
    assert "cluster_investigation_join" in merged["source_stages"]
    assert merged["verification"]["status"] == "degraded"
    assert merged["evidence_bundle_sha256"] != "old"
    children[0]["verdict"]["x"] = 99
    assert merged["cluster_investigation_results"]["children"][0]["verdict"]["x"] == 1
    assert source == _decision()


def test_merge_all_completed_preserves_complete_status_but_requires_review():
    merged = merge_terminal_child_projection(
        _decision(), [{"status": "completed", "failure_cluster_id": "a"}]
    )
    assert merged["status"] == "complete"
    assert merged["quality_review"]["requires_human_review"] is True
    assert merged["cluster_investigation_results"]["status"] == "complete"


def test_merge_empty_children_preserves_prior_decision_without_aliasing():
    source = _decision()
    merged = merge_terminal_child_projection(source, [])
    assert merged["cluster_investigation_results"]["selected_count"] == 0
    assert merged["status"] == "complete"
    assert merged is not source


def test_replay_validator_accepts_only_authoritative_delta():
    previous = _decision()
    previous["evidence_bundle_sha256"] = compute_decision_evidence_hash(previous)
    children = [{"status": "completed", "failure_cluster_id": "a"}]
    candidate = merge_terminal_child_projection(previous, children)
    result = validate_supersession_projection(
        previous_decision=previous,
        candidate_decision=candidate,
        children=children,
        project_id="project-1",
        test_run_id="run-1",
        previous_report={
            "project_id": "project-1",
            "test_run_id": "run-1",
            "verification": {"status": "passed"},
        },
    )
    assert result["status"] == "passed"


def test_replay_validator_rejects_tampered_child_or_previous_report():
    previous = _decision()
    previous["evidence_bundle_sha256"] = compute_decision_evidence_hash(previous)
    children = [{"status": "completed", "failure_cluster_id": "a"}]
    candidate = merge_terminal_child_projection(previous, children)
    candidate["cluster_investigation_results"]["children"][0]["status"] = "failed"
    result = validate_supersession_projection(
        previous_decision=previous,
        candidate_decision=candidate,
        children=children,
        previous_report={
            "project_id": "project-1",
            "test_run_id": "run-1",
            "verification": {"status": "passed"},
        },
    )
    assert result["status"] == "failed"
    assert "supersession_projection_mismatch" in result["failures"]

    previous["metrics"]["failed_tests"] = 99
    result = validate_supersession_projection(
        previous_decision=previous,
        candidate_decision=merge_terminal_child_projection(previous, children),
        children=children,
        previous_report={
            "project_id": "project-1",
            "test_run_id": "run-1",
            "verification": {"status": "passed"},
        },
    )
    assert result["status"] == "failed"
    assert "previous_report_evidence_hash_mismatch" in result["failures"]


@pytest.mark.asyncio
async def test_schedule_query_binds_parent_to_project_and_run(monkeypatch):
    class _Rows:
        def scalar_one_or_none(self):
            return None

    class _Session:
        def __init__(self):
            self.statements = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, statement):
            self.statements.append(str(statement))
            return _Rows()

    session = _Session()
    monkeypatch.setattr(
        "app.services.decision_report_supersession_service.AsyncSessionLocal",
        lambda: session,
    )
    with pytest.raises(ValueError, match="supersession_parent_not_found"):
        from app.services.decision_report_supersession_service import (
            schedule_decision_report_supersession,
        )

        await schedule_decision_report_supersession(
            project_id="00000000-0000-0000-0000-000000000001",
            test_run_id="00000000-0000-0000-0000-000000000002",
            parent_pipeline_run_id="00000000-0000-0000-0000-000000000003",
        )
    assert "test_runs.project_id" in session.statements[0]
    assert "agent_pipeline_runs.test_run_id" in session.statements[0]
