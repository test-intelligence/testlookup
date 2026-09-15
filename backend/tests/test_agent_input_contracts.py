"""Regression tests for the formerly label-only T21 catalog inputs."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.models.agent_input_contracts import (
    AuthoritativeFailureClusterV1,
    ClusterInvestigationExpansionPlanV1,
    InvestigationRequestV1,
)

ZERO_HASH = "0" * 64


def test_authoritative_cluster_rejects_duplicate_member_authority():
    member = uuid.uuid4()
    with pytest.raises(ValidationError, match="member_test_ids must be unique"):
        AuthoritativeFailureClusterV1(
            failure_cluster_id=uuid.uuid4(),
            cluster_id="cluster:1",
            member_test_ids=(member, member),
        )


def test_cluster_expansion_plan_rejects_a_false_count_projection():
    skipped = {
        "member_count": 0,
        "candidate_sha256": ZERO_HASH,
        "capability_id": "agent.cluster_investigation.v1",
        "selected": False,
        "skip_reason": "candidate_malformed",
        "rationale": "candidate failed strict validation",
        "dependencies": ["failure_clustering", "cluster_investigation_dispatch"],
        "permission": "read_only",
        "budget": {
            "max_llm_calls": 0,
            "max_tokens": 0,
            "max_cost_usd": 0,
            "max_seconds": 0,
        },
        "fallback": "deterministic_cluster_summary",
        "concurrency_class": "investigation_child",
    }
    base = {
        "planner_version": "cluster-investigation-planner:v1",
        "parent_pipeline_run_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "aggregate_budget": {
            "max_llm_calls": 0,
            "max_tokens": 0,
            "max_cost_usd": 0,
            "max_seconds": 0,
        },
        "limits": {"max_children": 1, "max_members": 50},
        "candidate_count": 1,
        "selected_count": 0,
        "skipped_count": 1,
        "selected": [],
        "skipped": [skipped],
        "tasks": [skipped],
        "dependencies": ["failure_clustering", "cluster_investigation_dispatch"],
        "fallback": "continue_without_cluster_children",
        "expansion_id": "cluster-plan:" + "0" * 20,
        "expansion_sha256": ZERO_HASH,
    }
    ClusterInvestigationExpansionPlanV1.model_validate(base)
    base["candidate_count"] = 2
    with pytest.raises(ValidationError, match="candidate_count"):
        ClusterInvestigationExpansionPlanV1.model_validate(base)


def test_investigation_request_requires_complete_cluster_scope_authority():
    base = {
        "investigation_id": uuid.uuid4(),
        "pipeline_run_id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "build_number": "build-42",
        "mode": "suggest",
        "triggered_by": "user@example.test",
        "scope_type": "failure_cluster",
    }
    with pytest.raises(ValidationError, match="requires a cluster and member IDs"):
        InvestigationRequestV1.model_validate(base)


def test_new_catalog_contracts_are_closed_to_unknown_fields():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AuthoritativeFailureClusterV1(
            failure_cluster_id=uuid.uuid4(),
            cluster_id="cluster:1",
            member_test_ids=(uuid.uuid4(),),
            unreviewed_payload=True,
        )
