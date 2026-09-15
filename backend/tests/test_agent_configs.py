"""E4.1: per-project agent configuration (architecture sections 4.1-4.3).

Pins:

* the schema: ``extra="forbid"`` everywhere (no provider endpoint or key can be
  stored), the environment ceilings, the composition rule with its arithmetic,
  and the mode checks for tools and for the capability itself;
* the defaults: every configurable agent's defaults validate, and a mutating
  capability is never granted ``act`` by default;
* the request layer: ``apply_patch`` refuses every loosening in the
  monotonicity table and honours ``override_policy``;
* the store: one upsert statement that bumps ``config_version``;
* the router: 404 / 422 paths, the offline provider courtesy check, the
  activity event, and the QA_LEAD guard on PUT;
* pipeline runs freeze the project's config versions into ``execution_metadata``.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.models.postgres import AGENT_CONFIG_MODES, UserRole
from app.routers import agent_configs as router_mod
from app.services import agent_config_service as svc

BACKEND = Path(__file__).resolve().parents[1]
PROJECT_ID = uuid.uuid4()
SUMMARY = "agent.summary.v1"
TRIAGE = "agent.triage.v1"
COMMANDER = "agent.defect_commander.v1"


def _config(**over):
    doc = svc.default_config(SUMMARY).model_dump()
    for key, value in over.items():
        doc[key] = value
    return doc


# -- vocabulary kept in step --------------------------------------------------------------


def test_modes_match_the_model_and_the_migration():
    spec = importlib.util.spec_from_file_location("m0179", BACKEND / "migrations" / "versions" / "0179_agent_configs.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert tuple(svc.MODE_ORDER) == tuple(AGENT_CONFIG_MODES) == tuple(migration.MODES)
    assert migration.down_revision == "0178"


def test_every_langchain_tool_has_a_declared_permission():
    names = set()
    for path in (BACKEND / "app" / "tools").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                (isinstance(d, ast.Name) and d.id == "tool") or (isinstance(d, ast.Call) and getattr(d.func, "id", "") == "tool")
                for d in node.decorator_list
            ):
                names.add(node.name)
    assert names, "the scan found no @tool functions"
    assert names == set(svc.AGENT_TOOL_PERMISSIONS)


# -- defaults ------------------------------------------------------------------------------------


def test_every_configurable_agent_has_valid_defaults():
    agents = svc.configurable_capabilities()
    assert "agent.workflow.v1" not in agents, "the runtime pseudo-capability has no configuration"
    for agent_id in agents:
        config = svc.default_config(agent_id)
        assert config.retry.max_attempts * config.timeout_seconds <= settings.AI_PIPELINE_DEADLINE_SECONDS


def test_defaults_stay_valid_under_lowered_ceilings(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_ATTEMPTS_CEILING", 2)
    monkeypatch.setattr(settings, "AGENT_MAX_TIMEOUT_CEILING", 45)
    monkeypatch.setattr(settings, "AI_PIPELINE_DEADLINE_SECONDS", 60)
    for agent_id in svc.configurable_capabilities():
        config = svc.default_config(agent_id)
        assert config.retry.max_attempts <= 2 and config.timeout_seconds <= 45
        assert config.retry.max_attempts * config.timeout_seconds <= 60
    assert svc.serialize(SUMMARY, None)["valid"] is True


def test_default_modes_follow_the_capability_permission():
    summary, triage, commander = (svc.default_config(a) for a in (SUMMARY, TRIAGE, COMMANDER))
    assert (summary.mode, summary.enabled) == ("shadow", True)
    assert (triage.mode, triage.enabled) == ("suggest", True)
    assert (commander.mode, commander.enabled) == ("shadow", False), "a mutating agent must not default to act"
    assert summary.tools.allowlist == svc.tools_permitted_by("shadow")
    assert summary.budget.max_cost_usd_per_run == 5.0 and summary.budget.max_runs_per_day == 10
    assert summary.retry.max_attempts == settings.AGENT_PIPELINE_MAX_ATTEMPTS


# -- schema: nothing extra, no endpoints ---------------------------------------------------------


@pytest.mark.parametrize("field", ["base_url", "api_key", "endpoint"])
def test_a_model_block_cannot_carry_an_endpoint_or_key(field):
    llm = {"provider": "ollama", "model": "qwen2.5:14b", field: "http://example.com"}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        svc.AgentConfigV1.model_validate(_config(model={"tier": "llm", "llm": llm}))


def test_unknown_top_level_keys_and_providers_are_refused():
    with pytest.raises(ValidationError, match="Extra inputs"):
        svc.AgentConfigV1.model_validate(_config(provider="openai"))
    with pytest.raises(ValidationError, match="unknown provider"):
        svc.AgentConfigV1.model_validate(_config(model={"llm": {"provider": "nope", "model": "m"}}))
    with pytest.raises(ValidationError, match="unknown agent_id"):
        svc.AgentConfigV1.model_validate(_config(agent_id="agent.workflow.v1"))


# -- schema: section 4.3 -------------------------------------------------------------------------


def test_composition_is_refused_with_the_arithmetic():
    doc = _config(timeout_seconds=300)
    doc["retry"]["max_attempts"] = 10
    with pytest.raises(ValidationError) as exc:
        svc.AgentConfigV1.model_validate(doc)
    message = str(exc.value)
    assert "10 x 300 = 3000 s" in message and "1500 s pipeline deadline" in message


def test_composition_at_the_deadline_is_accepted():
    doc = _config(timeout_seconds=300)
    doc["retry"]["max_attempts"] = 5
    assert svc.AgentConfigV1.model_validate(doc).timeout_seconds == 300


def test_environment_ceilings_are_enforced(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_ATTEMPTS_CEILING", 3)
    monkeypatch.setattr(settings, "AGENT_MAX_TIMEOUT_CEILING", 100)
    doc = _config(timeout_seconds=101)
    with pytest.raises(ValidationError, match="AGENT_MAX_TIMEOUT_CEILING=100"):
        svc.AgentConfigV1.model_validate(doc)
    doc = _config(timeout_seconds=10)
    doc["retry"]["max_attempts"] = 4
    with pytest.raises(ValidationError, match="AGENT_MAX_ATTEMPTS_CEILING=3"):
        svc.AgentConfigV1.model_validate(doc)
    doc["retry"]["max_attempts"] = 3
    assert svc.AgentConfigV1.model_validate(doc).retry.max_attempts == 3


def test_retry_on_takes_only_retryable_codes_and_cap_covers_base():
    doc = _config()
    doc["retry"]["retry_on"] = ["timeout", "validation_failed"]
    with pytest.raises(ValidationError, match="retryable codes"):
        svc.AgentConfigV1.model_validate(doc)
    doc = _config()
    doc["retry"].update(base_seconds=60, cap_seconds=30)
    with pytest.raises(ValidationError, match="cap_seconds=30 is below"):
        svc.AgentConfigV1.model_validate(doc)


def test_tools_need_a_mode_that_permits_them(monkeypatch):
    monkeypatch.setitem(svc.AGENT_TOOL_PERMISSIONS, "file_jira_ticket", "mutating")
    doc = _config(tools={"allowlist": ["file_jira_ticket"]})
    with pytest.raises(ValidationError, match="need a higher mode than 'shadow'"):
        svc.AgentConfigV1.model_validate(doc)
    doc["mode"] = "suggest"
    with pytest.raises(ValidationError, match="need a higher mode"):
        svc.AgentConfigV1.model_validate(doc)
    doc["mode"] = "act"
    assert svc.AgentConfigV1.model_validate(doc).tools.allowlist == ["file_jira_ticket"]
    with pytest.raises(ValidationError, match="unknown tools"):
        svc.AgentConfigV1.model_validate(_config(tools={"allowlist": ["rm_rf"]}))


def test_the_capability_permission_is_checked_against_mode():
    doc = svc.default_config(COMMANDER).model_dump()
    doc["enabled"] = True
    with pytest.raises(ValidationError, match="mutating capability; mode 'shadow' cannot run it"):
        svc.AgentConfigV1.model_validate(doc)
    doc["mode"] = "suggest"
    with pytest.raises(ValidationError, match="cannot run it"):
        svc.AgentConfigV1.model_validate(doc)
    doc["mode"] = "act"
    assert svc.AgentConfigV1.model_validate(doc).enabled
    triage = svc.default_config(TRIAGE).model_dump()
    triage["mode"] = "shadow"
    triage["tools"]["allowlist"] = []
    with pytest.raises(ValidationError, match="propose_action capability"):
        svc.AgentConfigV1.model_validate(triage)
    triage["enabled"] = False
    assert not svc.AgentConfigV1.model_validate(triage).enabled


def test_review_policy_with_auto_reviewer_requires_the_reviewer():
    with pytest.raises(ValidationError, match="requires review.auto_reviewer=true"):
        svc.AgentConfigV1.model_validate(_config(review={"policy": "human_required_plus_auto_reviewer", "auto_reviewer": False}))


# -- the request layer ---------------------------------------------------------------------------


def _base(**over):
    doc = svc.default_config(SUMMARY).model_dump()
    doc["model"]["tier"] = over.pop("tier", "slm")
    for key, value in over.items():
        doc[key] = value
    return svc.AgentConfigV1.model_validate(doc)


def _patch(**fields):
    return svc.AgentConfigPatch.model_validate(fields)


def test_tier_only_steps_down():
    assert svc.apply_patch(_base(), _patch(model={"tier": "deterministic"})).model.tier == "deterministic"
    with pytest.raises(svc.OverrideRejected, match="looser than the project tier 'slm'"):
        svc.apply_patch(_base(), _patch(model={"tier": "llm"}))
    with pytest.raises(svc.OverrideRejected, match="looser"):
        svc.apply_patch(_base(tier="llm"), _patch(model={"tier": "auto"}))
    assert svc.apply_patch(_base(tier="auto"), _patch(model={"tier": "llm"})).model.tier == "llm"


def test_override_policy_can_forbid_each_narrowing():
    locked = {"allow_tier_downgrade": False, "allow_retry_decrease": False, "allow_tool_narrowing": False}
    base = _base(override_policy=locked)
    with pytest.raises(svc.OverrideRejected, match="allow_tier_downgrade is false"):
        svc.apply_patch(base, _patch(model={"tier": "deterministic"}))
    with pytest.raises(svc.OverrideRejected, match="allow_retry_decrease is false"):
        svc.apply_patch(base, _patch(retry={"max_attempts": 1}))
    with pytest.raises(svc.OverrideRejected, match="allow_tool_narrowing is false"):
        svc.apply_patch(base, _patch(tools={"allowlist": base.tools.allowlist[:1]}))
    # Restating the project's own value is not a change.
    same = svc.apply_patch(base, _patch(model={"tier": "slm"}, retry={"max_attempts": base.retry.max_attempts}))
    assert same == base


def test_numbers_only_go_down():
    base = _base()
    lowered = svc.apply_patch(base, _patch(
        retry={"max_attempts": 2}, timeout_seconds=30, thresholds={"max_failures_analyzed": 10},
        budget={"max_tokens_per_run": 1000, "max_cost_usd_per_run": 0.5},
    ))
    assert (lowered.retry.max_attempts, lowered.timeout_seconds) == (2, 30)
    assert lowered.thresholds.max_failures_analyzed == 10
    assert (lowered.budget.max_tokens_per_run, lowered.budget.max_cost_usd_per_run) == (1000, 0.5)
    assert lowered.budget.max_runs_per_day == base.budget.max_runs_per_day
    for fields, name in [
        ({"retry": {"max_attempts": base.retry.max_attempts + 1}}, "retry.max_attempts"),
        ({"timeout_seconds": base.timeout_seconds + 1}, "timeout_seconds"),
        ({"thresholds": {"max_failures_analyzed": 51}}, "thresholds.max_failures_analyzed"),
        ({"budget": {"max_cost_usd_per_run": 5.01}}, "budget.max_cost_usd_per_run"),
    ]:
        with pytest.raises(svc.OverrideRejected, match=f"{name}=.* loosens"):
            svc.apply_patch(base, _patch(**fields))


def test_tools_only_narrow_and_review_only_adds():
    base = _base()
    narrowed = svc.apply_patch(base, _patch(tools={"allowlist": ["check_test_flakiness"]}))
    assert narrowed.tools.allowlist == ["check_test_flakiness"]
    small = _base(tools={"allowlist": ["check_test_flakiness"]})
    with pytest.raises(svc.OverrideRejected, match=r"\['query_splunk_logs'\] are not in the project allowlist"):
        svc.apply_patch(small, _patch(tools={"allowlist": ["check_test_flakiness", "query_splunk_logs"]}))
    reviewed = svc.apply_patch(base, _patch(review={"add_auto_reviewer": True}))
    assert (reviewed.review.policy, reviewed.review.auto_reviewer) == ("human_required_plus_auto_reviewer", True)
    assert svc.apply_patch(base, _patch(review={"add_auto_reviewer": False})).review == base.review


@pytest.mark.parametrize("fields", [
    {"mode": "act"},
    {"model": {"tier": "slm", "llm": {"provider": "ollama", "model": "m"}}},
    {"model": {"temperature": 1.0}},
    {"thresholds": {"confidence_min": 10}},
    {"review": {"policy": "human_required"}},
    {"override_policy": {"allow_tier_downgrade": True}},
])
def test_fields_outside_the_table_cannot_be_overridden(fields):
    with pytest.raises(ValidationError, match="Extra inputs"):
        _patch(**fields)


# -- environment courtesy -------------------------------------------------------------------------


def test_offline_refuses_cloud_providers_and_the_allowlist_applies(monkeypatch):
    doc = _config(model={"tier": "llm", "llm": {"provider": "openai", "model": "gpt"}, "slm": {"provider": "ollama", "model": "q"}})
    config = svc.AgentConfigV1.model_validate(doc)
    errors = svc.provider_environment_errors(config, offline=True)
    assert len(errors) == 1 and "model.llm.provider='openai' is not a local provider" in errors[0]
    assert svc.provider_environment_errors(config, offline=False) == []
    monkeypatch.setattr(settings, "AI_LLM_PROVIDER_ALLOWLIST", "ollama")
    (error,) = svc.provider_environment_errors(config, offline=False)
    assert "not in AI_LLM_PROVIDER_ALLOWLIST ['ollama']" in error


# -- the store ------------------------------------------------------------------------------------


def _row(**over):
    config = svc.default_config(SUMMARY).model_dump(mode="json", exclude={"agent_id", "enabled", "mode"})
    values = dict(
        agent_id=SUMMARY, enabled=True, mode="shadow", config=config, config_version=3,
        updated_at=datetime(2026, 9, 13, tzinfo=timezone.utc), updated_by=None,
    )
    values.update(over)
    return SimpleNamespace(**values)


async def test_put_is_one_upsert_that_bumps_the_version():
    seen = {}

    class _DB:
        async def execute(self, statement, execution_options=None):
            seen["sql"] = str(statement.compile(dialect=postgresql.dialect()))
            seen["params"] = statement.compile(dialect=postgresql.dialect()).params
            return SimpleNamespace(scalar_one=lambda: "row")

    config = svc.default_config(SUMMARY)
    assert await svc.put_config(_DB(), PROJECT_ID, config, updated_by=None) == "row"
    sql = seen["sql"]
    assert "ON CONFLICT ON CONSTRAINT uq_agent_configs_project_agent DO UPDATE" in sql
    assert "config_version = (agent_configs.config_version +" in sql
    assert "RETURNING" in sql
    assert seen["params"]["config_version"] == 1
    assert not {"agent_id", "enabled", "mode"} & set(seen["params"]["config"]), "mode has one home: its column"


async def test_config_versions_maps_configured_agents():
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(SUMMARY, 2), (TRIAGE, 7)])))
    assert await svc.config_versions(db, PROJECT_ID) == {SUMMARY: 2, TRIAGE: 7}


def test_serialize_marks_defaults_and_rows_that_stopped_validating():
    default = svc.serialize(SUMMARY, None)
    assert (default["source"], default["config_version"], default["valid"]) == ("default", 0, True)
    stored = svc.serialize(SUMMARY, _row())
    assert (stored["source"], stored["config_version"], stored["valid"]) == ("project", 3, True)
    assert stored["config"]["mode"] == "shadow" and stored["config"]["agent_id"] == SUMMARY
    broken = _row(config={**_row().config, "timeout_seconds": 900})
    out = svc.serialize(SUMMARY, broken)
    assert out["valid"] is False and any("AGENT_MAX_TIMEOUT_CEILING" in e for e in out["errors"])
    assert out["config"]["timeout_seconds"] == 900, "an invalid row is shown as stored, not replaced by defaults"


# -- the router -----------------------------------------------------------------------------------


def _user():
    return SimpleNamespace(id=uuid.uuid4(), role=UserRole.QA_LEAD, username="lead", email="lead@example.com")


async def test_get_unknown_agent_is_404_and_list_covers_every_agent(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        await router_mod.get_agent_config(PROJECT_ID, "agent.nope.v1", db=None, current_user=_user())
    assert exc.value.status_code == 404
    monkeypatch.setattr(svc, "list_config_rows", AsyncMock(return_value={SUMMARY: _row()}))
    payload = await router_mod.list_agent_configs(PROJECT_ID, db=None, current_user=_user())
    assert payload["tools"] == svc.AGENT_TOOL_PERMISSIONS and list(payload["tools"]) == sorted(payload["tools"])
    listed = payload["configs"]
    assert [c["agent_id"] for c in listed] == sorted(svc.configurable_capabilities())
    by_id = {c["agent_id"]: c for c in listed}
    assert by_id[SUMMARY]["source"] == "project" and by_id[TRIAGE]["source"] == "default"


async def test_put_refuses_a_mismatched_agent_and_an_offline_cloud_provider(monkeypatch):
    monkeypatch.setattr(router_mod, "get_effective_ai_config", AsyncMock(return_value={"offline_mode": True}))
    put = AsyncMock()
    monkeypatch.setattr(svc, "put_config", put)
    body = svc.default_config(SUMMARY)
    with pytest.raises(HTTPException) as exc:
        await router_mod.put_agent_config(PROJECT_ID, TRIAGE, body, db=None, current_user=_user(), _lead=None)
    assert exc.value.status_code == 422 and "does not match" in exc.value.detail
    cloud = svc.AgentConfigV1.model_validate(_config(model={"tier": "llm", "llm": {"provider": "openai", "model": "gpt"}}))
    with pytest.raises(HTTPException) as exc:
        await router_mod.put_agent_config(PROJECT_ID, SUMMARY, cloud, db=None, current_user=_user(), _lead=None)
    assert exc.value.status_code == 422 and "not a local provider" in exc.value.detail[0]
    put.assert_not_awaited()


async def test_put_writes_records_activity_and_commits(monkeypatch):
    user = _user()
    monkeypatch.setattr(router_mod, "get_effective_ai_config", AsyncMock(return_value={"offline_mode": True}))
    monkeypatch.setattr(svc, "get_config_row", AsyncMock(return_value=None))
    body = svc.AgentConfigV1.model_validate(_config(timeout_seconds=45))
    written = _row(config=body.model_dump(mode="json", exclude={"agent_id", "enabled", "mode"}), config_version=1)
    put = AsyncMock(return_value=written)
    monkeypatch.setattr(svc, "put_config", put)
    activity = AsyncMock()
    monkeypatch.setattr(router_mod, "record_activity", activity)
    db = SimpleNamespace(commit=AsyncMock())

    out = await router_mod.put_agent_config(PROJECT_ID, SUMMARY, body, db=db, current_user=user, _lead=user)

    assert out["config_version"] == 1 and out["config"]["timeout_seconds"] == 45
    assert put.await_args.kwargs["updated_by"] == user.id
    kwargs = activity.await_args.kwargs
    assert kwargs["event_type"] == "agent_config.updated" and kwargs["entity_label"] == SUMMARY
    assert kwargs["changed_fields"] == ["timeout_seconds"]
    assert kwargs["context"]["config_version"] == 1
    db.commit.assert_awaited_once()


async def test_compatibility_config_put_skips_registry_tier_gate(monkeypatch):
    """Investigator/Fixer model fields are inert compatibility scaffolding."""
    user = _user()
    body = svc.default_config("fixer")
    config = body.model_dump(mode="json", exclude={"agent_id", "enabled", "mode"})
    written = _row(agent_id="fixer", enabled=False, config=config, config_version=1)
    monkeypatch.setattr(router_mod, "get_effective_ai_config", AsyncMock(return_value={"offline_mode": True}))
    monkeypatch.setattr(svc, "get_config_row", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "put_config", AsyncMock(return_value=written))
    monkeypatch.setattr(router_mod, "record_activity", AsyncMock())
    db = SimpleNamespace(commit=AsyncMock())

    out = await router_mod.put_agent_config(
        PROJECT_ID,
        "fixer",
        body,
        db=db,
        current_user=user,
        _lead=user,
    )

    assert out["agent_id"] == "fixer"
    db.commit.assert_awaited_once()


def test_put_requires_qa_lead():
    dependency = inspect.signature(router_mod.put_agent_config).parameters["_lead"].default.dependency
    cells = [c.cell_contents for c in (dependency.__closure__ or ())]
    from app.core.deps import _ROLE_ORDER

    assert _ROLE_ORDER.index(UserRole.QA_LEAD) in cells
    get_params = inspect.signature(router_mod.get_agent_config).parameters
    assert "_lead" not in get_params, "reading a config needs project access only"


# -- pipeline runs freeze the versions ------------------------------------------------------------


async def test_a_pipeline_run_freezes_the_projects_config_versions(monkeypatch):
    from app.agents import workflow
    from app.models.postgres import AgentPipelineRun
    from app.services import agent_investigation_service, feature_flags

    added = []

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def add(self, row):
            added.append(row)

        async def commit(self):
            return None

    monkeypatch.setattr(workflow, "AsyncSessionLocal", lambda: _Session())
    monkeypatch.setattr(feature_flags, "is_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(agent_investigation_service, "get_effective_policy", AsyncMock(return_value={"budgets": {}}))
    monkeypatch.setattr(svc, "config_versions", AsyncMock(return_value={SUMMARY: 4}))

    requester = uuid.uuid4()
    await workflow._create_pipeline_run(
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(PROJECT_ID),
        "offline",
        requested_by=str(requester),
    )

    (run,) = [row for row in added if isinstance(row, AgentPipelineRun)]
    assert run.execution_metadata["agent_config_versions"] == {SUMMARY: 4}
    assert run.requested_by == requester
    from app.services.eval_provenance_service import current_eval_manifest_checksum

    assert run.execution_metadata["eval_manifest_checksum"] == current_eval_manifest_checksum()


async def test_an_invocation_run_uses_and_persists_its_frozen_config(monkeypatch):
    from app.agents import workflow
    from app.models.postgres import AgentPipelineRun
    from app.services import agent_config_resolver, agent_investigation_service, feature_flags

    added = []

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def add(self, row):
            added.append(row)

        async def commit(self):
            return None

    patch = svc.AgentConfigPatch.model_validate({
        "retry": {"max_attempts": 2},
        "timeout_seconds": 30,
        "budget": {
            "max_llm_calls_per_run": 3,
            "max_tokens_per_run": 4000,
            "max_cost_usd_per_run": 0.5,
        },
        "review": {"add_auto_reviewer": True},
    })
    snapshot = agent_config_resolver.freeze_for_invocation(
        agent_config_resolver.resolve(
            SUMMARY,
            global_ai_config={},
            config_version=9,
            patch=patch,
        )
    )
    # A ceiling tightened after request acceptance still wins at worker start.
    monkeypatch.setattr(settings, "AGENT_MAX_ATTEMPTS_CEILING", 1)
    monkeypatch.setattr(workflow, "AsyncSessionLocal", lambda: _Session())
    monkeypatch.setattr(feature_flags, "is_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(
        agent_investigation_service,
        "get_effective_policy",
        AsyncMock(return_value={"budgets": {}}),
    )
    monkeypatch.setattr(svc, "config_versions", AsyncMock(return_value={SUMMARY: 4}))

    await workflow._create_pipeline_run(
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(PROJECT_ID),
        "offline",
        invocation_stage="summary",
        invocation_config_snapshot=snapshot,
    )

    (run,) = [row for row in added if isinstance(row, AgentPipelineRun)]
    metadata = run.execution_metadata
    assert (run.max_attempts, run.review_policy) == (
        1,
        "human_required_plus_auto_reviewer",
    )
    assert metadata["agent_config_versions"][SUMMARY] == 9
    assert metadata["resolved_agent_configs"][SUMMARY] == snapshot
    assert metadata["run_budget"] == {
        "max_llm_calls": 3,
        "max_tokens": 4000,
        "max_cost_usd": 0.5,
        "max_seconds": 30,
    }
