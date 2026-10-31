"""Migration 0208 (AIC-07, D20): the six deep-pipeline stage flags get rows.

Pins the shape without a database: the six keys with their exact descriptions,
which ones are ON for a fresh install, which legacy env variable can turn one
ON on an existing install, the INSERT (``ON CONFLICT (key) DO NOTHING``, the
0201 fresh-install predicate verbatim) and the downgrade that deletes only a
row still equal to its seed. The Postgres behaviour runs in
``tests/integration/test_migration_0207_0209_postgres.py``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "0208_seed_agent_stage_flags.py"
FRESH_INSTALL = "NOT EXISTS (SELECT 1 FROM users) AND NOT EXISTS (SELECT 1 FROM projects)"

EXPECTED = {
    "contract_validation": (
        "API-contract specialist stage of the deep pipeline (read-only).",
        True,
        "AIQ_CONTRACT_VALIDATION_ENABLED",
    ),
    "log_intelligence": (
        "Log and trace evidence stage of the deep pipeline (read-only; needs Splunk, "
        "which stays off while AI_OFFLINE_MODE is on).",
        True,
        None,
    ),
    "regression_watchman": (
        "Classifies failures as new regression, known-flaky recurrence or environmental "
        "anomaly (read-only; may use the LLM).",
        True,
        None,
    ),
    "change_ownership": (
        "Baseline-diff and ownership stage of the deep pipeline (read-only).",
        True,
        "AIQ_CHANGE_OWNERSHIP_ENABLED",
    ),
    "defect_commander": (
        "MUTATING deep-pipeline stage: promotes high-confidence failure clusters to "
        "defects. Off until Act mode routes its effects through approvals.",
        False,
        None,
    ),
    "async_decision_report_supersession": (
        "Asynchronous supersession of decision reports in deep pipelines.",
        False,
        "AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED",
    ),
}
LEGACY_VARS = [v for _d, _f, v in EXPECTED.values() if v]


def _module():
    spec = importlib.util.spec_from_file_location("migration_0208", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clean_env(monkeypatch):
    for name in LEGACY_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_it_follows_0207():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0208", "0207")
    assert "Revision ID: 0208" in migration.__doc__ and "Revises: 0207" in migration.__doc__


def test_the_six_rows_their_descriptions_and_defaults():
    migration = _module()
    rows = {key: (description, fresh_on, env) for key, description, fresh_on, env in migration._FLAGS}
    assert rows == EXPECTED
    assert migration._STAGES is migration._FLAGS
    for description, _fresh, _env in rows.values():
        assert description.isascii()


def test_the_legacy_variables_are_the_feature_flags_map():
    """A seeded OFF row must not switch off a stage enabled through its env fallback."""
    from app.services.feature_flags import LEGACY_ENV_VAR_MAP

    migration = _module()
    for key, _description, _fresh, env in migration._FLAGS:
        assert env == LEGACY_ENV_VAR_MAP.get(key), key


def test_the_insert_is_idempotent_and_uses_0201s_predicate():
    migration = _module()
    assert migration.FRESH_INSTALL == FRESH_INSTALL
    sql = migration.INSERT_SQL
    assert sql.startswith(
        "INSERT INTO feature_flags "
        "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
        "SELECT gen_random_uuid(), :key, :description, "
    )
    assert "((CAST(:fresh_on AS boolean)) AND (" + FRESH_INSTALL + ")) OR CAST(:env_on AS boolean)" in sql
    assert "100, now(), now()" in sql
    assert sql.endswith("ON CONFLICT (key) DO NOTHING")


def test_without_env_only_the_four_read_only_stages_are_fresh_install_on(clean_env):
    migration = _module()
    params = {p["key"]: p for p in migration.insert_params()}
    assert set(params) == set(EXPECTED)
    assert {k for k, p in params.items() if p["fresh_on"]} == {
        "contract_validation", "log_intelligence", "regression_watchman", "change_ownership",
    }
    assert not any(p["env_on"] for p in params.values())
    for key, p in params.items():
        assert p["description"] == EXPECTED[key][0]


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "On"])
def test_a_truthy_legacy_variable_turns_its_row_on(clean_env, value):
    clean_env.setenv("AIQ_CONTRACT_VALIDATION_ENABLED", value)
    clean_env.setenv("AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED", value)
    migration = _module()
    params = {p["key"]: p for p in migration.insert_params()}
    assert params["contract_validation"]["env_on"] is True
    assert params["async_decision_report_supersession"]["env_on"] is True
    assert params["change_ownership"]["env_on"] is False
    # defect_commander has no legacy variable: it can never be seeded ON (D20).
    assert params["defect_commander"] == {
        "key": "defect_commander",
        "description": EXPECTED["defect_commander"][0],
        "fresh_on": False,
        "env_on": False,
    }


@pytest.mark.parametrize("value", ["", "0", "false", "off", "no", "enabled"])
def test_a_falsy_legacy_variable_leaves_its_row_off(clean_env, value):
    clean_env.setenv("AIQ_CHANGE_OWNERSHIP_ENABLED", value)
    migration = _module()
    assert migration._env_on("AIQ_CHANGE_OWNERSHIP_ENABLED") is False
    assert migration._env_on(None) is False


def test_upgrade_inserts_each_row_with_its_bound_values(clean_env):
    clean_env.setenv("AIQ_CHANGE_OWNERSHIP_ENABLED", "true")
    migration = _module()
    migration.op = MagicMock()
    migration.upgrade()
    calls = migration.op.execute.call_args_list
    assert len(calls) == 6
    seen = {}
    for c in calls:
        clause = c.args[0]
        assert str(clause) == migration.INSERT_SQL
        values = {name: bp.value for name, bp in clause._bindparams.items()}
        seen[values["key"]] = values
    assert seen["change_ownership"]["env_on"] is True
    assert seen["log_intelligence"] == {
        "key": "log_intelligence",
        "description": EXPECTED["log_intelligence"][0],
        "fresh_on": True,
        "env_on": False,
    }


def test_downgrade_deletes_only_untouched_seed_rows():
    migration = _module()
    assert migration.DELETE_SQL == (
        "DELETE FROM feature_flags WHERE key = :key AND description = :description "
        "AND created_at = updated_at AND enabled_projects IS NULL AND enabled_roles IS NULL "
        "AND rollout_percent = 100"
    )
    migration.op = MagicMock()
    migration.downgrade()
    deleted = []
    for c in migration.op.execute.call_args_list:
        clause = c.args[0]
        assert str(clause) == migration.DELETE_SQL
        values = {name: bp.value for name, bp in clause._bindparams.items()}
        deleted.append((values["key"], values["description"]))
    assert deleted == [(k, d) for k, (d, _f, _e) in EXPECTED.items()]
