"""W14 AGT-07: a deep run configuration on ``POST /deep-investigate/{run_id}``.

The configuration is ONE tighten-only ``AgentConfigPatch`` on the Root Cause
agent (tier, ``max_failures_analyzed``, the tool allowlist) applied through the
resolver's own patch path, plus a failure subset; every refusal is a 422 BEFORE
anything is queued. Pinned over a scripted session and the REAL pure resolver
(``agent_config_resolver.resolve``: ``apply_patch`` decides what loosens):

* loosening the tier / the cap / the tools -> 422 ``RUN_CONFIG_TIGHTEN_ONLY`` +
  ``errors``; a bad tier word -> 422; a passing result or another run's failure
  in ``failure_ids`` -> 422 with the count; ``mode: offline`` + a field -> 422;
* no configuration -> the ``apply_async`` kwargs are exactly today's (no
  ``run_overrides`` key); a configuration -> ``run_overrides`` in the kwargs;
* a deep pipeline in progress -> 200 ``already_in_progress`` and nothing queued;
* ``analysis.triggered`` recorded (attempt mode) with ``run_configuration``;
* every queued payload BOUND to ``inspect.signature(run_agent_pipeline.run)``.
No model is called and ``get_llm`` is never mocked (nothing reaches it).
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.core.agent_ops_vocab import (
    DEEP_ALREADY_IN_PROGRESS,
    RUN_CONFIG_AGENT_ID,
    RUN_CONFIG_DEEP_ONLY,
    RUN_CONFIG_FAILURES_OUTSIDE,
    RUN_CONFIG_TIER_INVALID,
    RUN_CONFIG_TIGHTEN_ONLY,
)

PROJECT = uuid.uuid4()
RUN = uuid.uuid4()
AI = {"provider": "ollama", "model": "qwen2.5:7b", "offline_mode": False}


def _stored(**changes: Any) -> dict:
    """The project's Root Cause config: tier slm, at most 20 failures, the default tools."""
    from app.services.agent_config_service import default_config

    doc = default_config(RUN_CONFIG_AGENT_ID).model_dump()
    doc["model"]["tier"] = "slm"
    doc["thresholds"]["max_failures_analyzed"] = 20
    for key, value in changes.items():
        doc[key] = value
    return doc


def _project_tools() -> list[str]:
    from app.services.agent_config_service import default_config

    return list(default_config(RUN_CONFIG_AGENT_ID).tools.allowlist)


class _Session:
    """select(TestRun) -> the run; the subset count -> ``inside``; the in-progress reads."""

    def __init__(self, *, inside: int = 0, running: Any = None) -> None:
        self.run = SimpleNamespace(
            id=RUN, project_id=PROJECT, build_number="b-7", failed_tests=30, broken_tests=2,
        )
        self.inside = inside
        self.running = running
        self.statements: list[Any] = []

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(stmt)
        name = str(stmt.column_descriptions[0]["name"])
        if name == "TestRun":
            return SimpleNamespace(scalar_one_or_none=lambda: self.run)
        if name == "count":
            return SimpleNamespace(scalar_one=lambda: self.inside)
        if name == "test_run_id":   # runs_with_in_progress_pipeline: one column
            ids = [RUN] if self.running is not None else []
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ids))
        if name == "AgentPipelineRun":
            return SimpleNamespace(scalar_one_or_none=lambda: self.running)
        raise AssertionError(f"unexpected statement {name}")


@pytest.fixture
def harness(monkeypatch):
    """The real route with the real pure resolver over a stored project config."""
    from app.routers import deep_investigation
    from app.services import ai_config_resolver, deep_run_overrides
    from app.services.agent_config_resolver import resolve
    from app.worker.tasks import run_agent_pipeline

    state: dict[str, Any] = {"stored": _stored(), "queued": [], "events": []}

    async def _resolve_for_project(db, project_id, agent_id, *, patch=None, global_ai_config=None):
        assert project_id == PROJECT
        return resolve(agent_id, global_ai_config=global_ai_config, stored=state["stored"],
                       config_version=3, patch=patch)

    async def _gate(_db):
        return None

    async def _config(**_kwargs):
        return dict(AI)

    async def _record(db, **kwargs):
        state["events"].append(kwargs)

    def _apply_async(*, kwargs, queue):
        signature = inspect.signature(run_agent_pipeline.run)
        signature.bind(**kwargs)              # Celery's own argument check
        state["queued"].append({"kwargs": kwargs, "queue": queue})
        return SimpleNamespace(id="task-7")

    monkeypatch.setattr(deep_run_overrides, "resolve_for_project", _resolve_for_project)
    monkeypatch.setattr(deep_investigation, "require_deep_investigation", _gate)
    monkeypatch.setattr(ai_config_resolver, "get_effective_ai_config", _config)
    monkeypatch.setattr(deep_investigation, "record_activity", _record)
    monkeypatch.setattr(deep_investigation.run_agent_pipeline, "apply_async", _apply_async)

    async def trigger(session: _Session | None = None, **body: Any):
        db = session or _Session()
        request = deep_investigation.TriggerDeepRequest(**body)
        user = SimpleNamespace(id=uuid.uuid4(), username="qa", full_name="Q A", role="QA_ENGINEER")
        response = await deep_investigation.trigger_deep_investigation(
            RUN, request, current_user=user, _=user, db=db,
        )
        return response, db

    state["trigger"] = trigger
    return state


async def _refused(harness, **body: Any) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        await harness["trigger"](**body)
    assert harness["queued"] == [], "a refused configuration queued a pipeline"
    assert harness["events"] == []
    return caught.value


# ── refusals: 422 before anything is queued ─────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"model_tier": "llm"}, "model.tier='llm' is looser than the project tier 'slm'"),
        ({"max_failures_analyzed": 21}, "thresholds.max_failures_analyzed=21 loosens the project value 20"),
        ({"evidence_sources": ["not_a_project_tool"]}, "not in the project allowlist"),
    ],
    ids=["tier", "cap", "tools"],
)
async def test_loosening_is_refused_with_the_invoke_text(harness, body, fragment):
    refused = await _refused(harness, **body)
    assert refused.status_code == 422
    assert refused.detail["message"] == RUN_CONFIG_TIGHTEN_ONLY
    assert any(fragment in reason for reason in refused.detail["errors"]), refused.detail


@pytest.mark.asyncio
async def test_a_policy_that_forbids_downgrade_refuses_a_lower_tier(harness):
    stored = _stored()
    stored["override_policy"] = {**stored["override_policy"], "allow_tier_downgrade": False}
    harness["stored"] = stored
    refused = await _refused(harness, model_tier="deterministic")
    assert refused.detail["errors"] == ["override_policy.allow_tier_downgrade is false for this agent"]


@pytest.mark.asyncio
async def test_an_unknown_tier_word_is_refused(harness):
    refused = await _refused(harness, model_tier="huge")
    assert (refused.status_code, refused.detail) == (422, RUN_CONFIG_TIER_INVALID)


@pytest.mark.asyncio
async def test_failures_outside_the_runs_failing_results_are_counted(harness):
    selected = [uuid.uuid4() for _ in range(3)]
    with pytest.raises(HTTPException) as caught:
        # one is failing in this run; one passed; one belongs to another run
        await harness["trigger"](_Session(inside=1), failure_ids=selected)
    assert (caught.value.status_code, caught.value.detail) == (
        422, RUN_CONFIG_FAILURES_OUTSIDE.format(n=2),
    )
    assert harness["queued"] == []


@pytest.mark.asyncio
async def test_a_configuration_on_an_offline_run_is_refused(harness):
    refused = await _refused(harness, mode="offline", max_failures_analyzed=5)
    assert (refused.status_code, refused.detail) == (422, RUN_CONFIG_DEEP_ONLY)


# ── accepted ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_configuration_queues_todays_payload(harness):
    response, db = await harness["trigger"](mode="deep")

    [queued] = harness["queued"]
    assert queued["kwargs"] == {
        "test_run_id": str(RUN), "project_id": str(PROJECT), "build_number": "b-7",
        "workflow_type": "deep", "requested_by": queued["kwargs"]["requested_by"],
    }
    assert "run_overrides" not in queued["kwargs"]
    assert response.run_configuration is None and response.already_in_progress is False
    # Unconfigured: the config is not even resolved (no subset count either).
    assert [str(s.column_descriptions[0]["name"]) for s in db.statements] == ["TestRun", "test_run_id"]
    [event] = harness["events"]
    assert event["event_type"] == "analysis.triggered"
    assert event["context"]["workflow_type"] == "deep"
    assert event["context"]["workflow_ref"] == "default:deep"
    assert event["context"]["run_configuration"] == "every failure · project configuration"


@pytest.mark.asyncio
async def test_a_tightened_configuration_is_queued_and_recorded(harness):
    selected = [uuid.uuid4() for _ in range(12)]
    tools = _project_tools()[:1]
    response, _db = await harness["trigger"](
        _Session(inside=12), failure_ids=selected, model_tier="deterministic",
        max_failures_analyzed=10, evidence_sources=tools,
    )

    [queued] = harness["queued"]
    overrides = queued["kwargs"]["run_overrides"]
    assert overrides["failure_ids"] == [str(i) for i in selected]
    assert overrides["agent_patches"] == {RUN_CONFIG_AGENT_ID: {
        "model": {"tier": "deterministic"},
        "thresholds": {"max_failures_analyzed": 10},
        "tools": {"allowlist": sorted(tools)},
    }}
    config = response.run_configuration
    assert (config.selected_failures, config.failures_analysed) == (12, 10)
    assert (config.max_failures_analyzed, config.project_max_failures_analyzed) == (10, 20)
    assert (config.model_tier, config.project_model_tier) == ("deterministic", "slm")
    assert config.allowed_tiers == ["deterministic", "slm"]
    assert config.evidence_sources == sorted(tools)
    [event] = harness["events"]
    assert event["context"]["run_configuration"] == (
        "12 failures · tier deterministic · at most 10 analysed · 1 evidence sources"
    )


@pytest.mark.asyncio
async def test_a_deep_pipeline_in_progress_is_answered_not_queued_again(harness):
    running = SimpleNamespace(id=uuid.uuid4(), status="running")
    response, _db = await harness["trigger"](_Session(running=running), mode="deep")

    assert response.already_in_progress is True
    assert response.message == DEEP_ALREADY_IN_PROGRESS
    assert response.pipeline_run_id == str(running.id) and response.task_id is None
    assert harness["queued"] == [] and harness["events"] == []


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_build_patch_is_none_when_nothing_is_tightened():
    from app.services.deep_run_overrides import DeepRunConfiguration, build_patch

    assert build_patch(DeepRunConfiguration()) is None
    assert build_patch(DeepRunConfiguration(failure_ids=(uuid.uuid4(),))) is None
    assert build_patch(DeepRunConfiguration(model_tier="slm")).model_dump(exclude_none=True) == {
        "model": {"tier": "slm"},
    }


def test_allowed_tiers_step_down_from_the_project_tier():
    from app.services.agent_config_resolver import resolve
    from app.services.deep_run_overrides import allowed_tiers

    def tiers(tier: str, downgrade: bool = True) -> list[str]:
        stored = _stored()
        stored["model"]["tier"] = tier
        stored["override_policy"] = {**stored["override_policy"], "allow_tier_downgrade": downgrade}
        return allowed_tiers(resolve(RUN_CONFIG_AGENT_ID, global_ai_config=AI, stored=stored))

    assert tiers("auto") == ["deterministic", "slm", "llm", "auto"]
    assert tiers("slm") == ["deterministic", "slm"]
    assert tiers("llm", downgrade=False) == ["llm"]
