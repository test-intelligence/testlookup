"""T5/E4.4 regression coverage for retiring agent_policies."""

from __future__ import annotations

import importlib
import uuid
from pathlib import Path

from app.models.postgres import AgentConfig
from app.services import agent_config_service, agent_investigation_service, fixer_service

MIGRATION = Path(__file__).resolve().parents[1] / "migrations/versions/0187_agent_policy_configs.py"


def test_migration_moves_both_rows_and_has_a_real_downgrade() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    upgrade, downgrade = source.split("def downgrade() -> None:")
    assert 'down_revision = "0186"' in source
    assert "FROM agent_policies AS p" in upgrade
    assert "refusing lossy E4.4 migration" in upgrade
    assert "p.agent_id IN ('investigator', 'fixer')" in upgrade
    assert "uq_agent_configs_project_agent" in upgrade
    assert 'op.drop_table("agent_policies")' in upgrade
    assert 'op.create_table(\n        "agent_policies"' in downgrade
    assert "FROM agent_configs AS c" in downgrade
    assert "DELETE FROM agent_configs WHERE agent_id IN ('investigator', 'fixer')" in downgrade


def test_agent_policy_model_is_retired_with_the_table() -> None:
    postgres = importlib.import_module("app.models.postgres")
    assert not hasattr(postgres, "AgentPolicy")
    app_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (Path(__file__).resolve().parents[1] / "app").rglob("*.py")
    )
    assert "select(AgentPolicy" not in app_source
    assert "from app.models.postgres import AgentPolicy" not in app_source


def test_scheduled_fixer_dispatch_reads_the_canonical_extension() -> None:
    worker = (Path(__file__).resolve().parents[1] / "app/worker/tasks.py").read_text(encoding="utf-8")
    assert 'AgentConfig.config["extensions"]["fixer"]["schedule"].astext == schedule' in worker
    assert "select(AgentPolicy.project_id)" not in worker


def test_investigator_extension_preserves_pinned_policy_and_all_runtime_budgets() -> None:
    budgets = {
        **agent_investigation_service.DEFAULT_BUDGETS,
        "max_runs_per_day": 4,
        "max_seconds_per_run": 91,
        "max_cluster_children_per_run": 3,
        "max_cluster_child_cost_usd_per_parent": 1.25,
    }
    row = AgentConfig(
        id=uuid.uuid4(), project_id=uuid.uuid4(), agent_id="investigator",
        enabled=False, mode="suggest", config={"extensions": {"investigator": {
            "budgets": budgets, "shadow_runs_completed": 17, "promotion_note": "ready",
        }}}, config_version=1,
    )
    policy = agent_investigation_service.serialize_policy("investigator", row)
    assert set(policy) == {"agent_id", "enabled", "mode", "budgets", "promotion"}
    assert policy["budgets"] == budgets
    assert agent_investigation_service.run_budget_from_policy(policy) == {
        "max_llm_calls": 30,
        "max_tokens": 60_000,
        "max_cost_usd": 5.0,
        "max_seconds": 91,
    }
    assert policy["promotion"] == {"shadow_runs_completed": 17, "note": "ready"}


def test_fixer_extension_preserves_pinned_config() -> None:
    config = agent_config_service.default_config("fixer")
    document = config.model_dump(mode="json")
    document["enabled"] = True
    document["mode"] = "suggest"
    document["extensions"]["fixer"].update({
        "runner": {"type": "docker", "runner_image": "python:3.12", "command_template": "pytest {test_selector}"},
        "test_globs": ["tests/**"],
        "budgets": {"max_tests_per_run": 8, "max_attempts_per_test": 2,
                    "validation_reruns": 6, "max_concurrent_open_prs": 3},
        "schedule": "weekly",
    })
    validated = agent_config_service.AgentConfigV1.model_validate(document)
    row = AgentConfig(
        id=uuid.uuid4(), project_id=uuid.uuid4(), agent_id="fixer",
        enabled=True, mode="suggest",
        config=validated.model_dump(mode="json", exclude={"agent_id", "enabled", "mode"}),
        config_version=1,
    )
    pinned = fixer_service.serialize_fixer_config(row)
    assert set(pinned) == {"enabled", "mode", "runner", "test_globs", "budgets", "schedule"}
    assert pinned["runner"]["runner_image"] == "python:3.12"
    assert pinned["budgets"]["max_tests_per_run"] == 8
    assert pinned["schedule"] == "weekly"


def test_compatibility_extensions_remain_strict_and_agent_scoped() -> None:
    investigator = agent_config_service.default_config("investigator").model_dump(mode="json")
    investigator["extensions"]["investigator"]["api_key"] = "secret"
    try:
        agent_config_service.AgentConfigV1.model_validate(investigator)
    except ValueError as exc:
        assert "Extra inputs are not permitted" in str(exc)
    else:
        raise AssertionError("an extension accepted an undeclared credential field")

    fixer = agent_config_service.default_config("fixer").model_dump(mode="json")
    fixer["mode"] = "act"
    try:
        agent_config_service.AgentConfigV1.model_validate(fixer)
    except ValueError as exc:
        assert "mode 'act' is reserved" in str(exc)
    else:
        raise AssertionError("the fixer accepted reserved act mode")
