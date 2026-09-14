"""E1.1: the agent catalog (architecture section 3).

The catalog is a projection of the capability registry, so these tests hold the
two together: every registered capability is published, the sync-eligible and
report flags come from the registry's own declarations, every capability gets a
generated input wrapper whose JSON Schema renders, and the routes answer 422 for
a malformed id and 404 for an unknown one.
"""
from __future__ import annotations

import inspect
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.services import agent_catalog
from app.services.agent_capability_registry import (
    CAPABILITY_REGISTRY,
    DEFAULT_TIERS,
    ESCALATION_TRIGGERS,
    REPORT_OUTPUT_SCHEMAS,
    SYNC_ELIGIBLE,
)

AGENT_IDS = sorted(spec.capability_id for spec in CAPABILITY_REGISTRY.values())


def test_every_registered_capability_is_published():
    assert [entry.agent_id for entry in agent_catalog.list_catalog()] == AGENT_IDS
    for entry in agent_catalog.list_catalog():
        assert entry.default_tier == DEFAULT_TIERS[entry.stage_name]
        assert entry.escalation == sorted(ESCALATION_TRIGGERS[entry.stage_name])


def test_root_cause_catalog_publishes_multi_artifact_escalation():
    entry = agent_catalog.get_catalog_detail("agent.root_cause_analysis.v1")

    assert entry is not None
    assert "multi_artifact_evidence" in entry.escalation


def test_reviewer_catalog_contract_is_resolved_and_deterministic():
    entry = agent_catalog.get_catalog_detail("agent.reviewer.v1")

    assert entry is not None
    assert entry.stage_name == "reviewer"
    assert entry.execution == "on_demand"
    assert entry.default_tier == "deterministic"
    assert entry.expected_cost_usd == 0
    assert entry.input_schema == "ReviewerInputV1" and entry.input_schema_resolved
    assert entry.output_schema == "ReviewVerdictV1" and entry.output_schema_resolved


def test_sync_eligibility_is_the_explicit_registry_flag_and_only_cheap_agents_have_it():
    entries = agent_catalog.list_catalog()
    assert {e.stage_name for e in entries if e.sync_eligible} == set(SYNC_ELIGIBLE)
    for entry in entries:
        if entry.sync_eligible:
            assert entry.expected_latency_ms <= 5_000, entry.agent_id
    assert SYNC_ELIGIBLE <= set(CAPABILITY_REGISTRY), "a sync-eligible name no capability has"


def test_report_producers_are_flagged_by_output_contract():
    entries = agent_catalog.list_catalog()
    assert {e.stage_name for e in entries if e.produces_report} == {
        name for name, spec in CAPABILITY_REGISTRY.items() if spec.output_schema in REPORT_OUTPUT_SCHEMAS
    }


@pytest.mark.parametrize("agent_id", AGENT_IDS)
def test_every_capability_has_a_detail_whose_schemas_render(agent_id):
    detail = agent_catalog.get_catalog_detail(agent_id)
    assert detail is not None
    assert agent_catalog.AGENT_ID_PATTERN.match(detail.agent_id)
    assert detail.input_json_schema["title"] == detail.input_wrapper
    assert detail.input_wrapper.endswith("InvokeInput")
    assert (detail.output_json_schema is not None) == detail.output_schema_resolved


def test_a_resolved_input_accepts_its_model_or_a_subject_ref_and_nothing_else():
    wrapper = agent_catalog.input_wrapper("agent.anomaly_detection.v1")
    entry = agent_catalog.get_catalog_detail("agent.anomaly_detection.v1")
    assert entry.input_schema == "RunEvidenceBundleV1" and entry.input_schema_resolved

    subject = {"test_run_id": str(uuid.uuid4())}
    wrapper.model_validate({"agent_id": "agent.anomaly_detection.v1", "payload": subject})
    with pytest.raises(ValidationError):
        wrapper.model_validate({"agent_id": "agent.summary.v1", "payload": subject})
    with pytest.raises(ValidationError):
        wrapper.model_validate({"agent_id": "agent.anomaly_detection.v1", "payload": subject, "extra": 1})


def test_an_unresolved_input_accepts_only_a_subject_ref():
    entry = agent_catalog.get_catalog_detail("agent.ingestion.v1")
    assert entry.input_schema == "TestRun" and entry.input_schema_resolved is False
    wrapper = agent_catalog.input_wrapper("agent.ingestion.v1")
    wrapper.model_validate({"agent_id": "agent.ingestion.v1", "payload": {"test_run_id": str(uuid.uuid4())}})
    with pytest.raises(ValidationError):
        wrapper.model_validate({"agent_id": "agent.ingestion.v1", "payload": {"anything": 1}})


def test_a_list_input_wraps_a_list_of_its_model():
    entry = agent_catalog.get_catalog_detail("agent.investigator_synthesis.v1")
    assert entry.input_schema == "AgentFindingV1[]" and entry.input_schema_resolved


def test_an_unknown_agent_has_no_detail():
    assert agent_catalog.get_catalog_detail("agent.nope.v1") is None
    with pytest.raises(KeyError):
        agent_catalog.input_wrapper("agent.nope.v1")


# -- routes -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_list_route_returns_the_catalog():
    from app.routers.agents import list_agent_catalog

    entries = await list_agent_catalog(_=None)
    assert [e.agent_id for e in entries] == AGENT_IDS


@pytest.mark.asyncio
async def test_the_detail_route_answers_422_for_a_malformed_id_and_404_for_an_unknown_one():
    from app.routers.agents import get_agent_catalog_entry

    with pytest.raises(HTTPException) as malformed:
        await get_agent_catalog_entry(agent_id="summary", _=None)
    assert malformed.value.status_code == 422
    with pytest.raises(HTTPException) as unknown:
        await get_agent_catalog_entry(agent_id="agent.nope.v1", _=None)
    assert unknown.value.status_code == 404
    detail = await get_agent_catalog_entry(agent_id="agent.summary.v1", _=None)
    assert detail.produces_report is True


def test_both_catalog_routes_require_an_authenticated_user():
    from app.core.deps import get_current_active_user
    from app.routers.agents import get_agent_catalog_entry, list_agent_catalog

    for handler in (list_agent_catalog, get_agent_catalog_entry):
        default = inspect.signature(handler).parameters["_"].default
        assert getattr(default, "dependency", None) is get_current_active_user, handler.__name__
