"""T3 schema contract for durable pipeline trigger attribution."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

from app.models.postgres import AgentPipelineRun

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0188_pipeline_requested_by.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("migration_0188", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_adds_nullable_set_null_user_attribution_and_reverses_it():
    migration = _module()
    migration.op = MagicMock()

    migration.upgrade()

    assert migration.down_revision == "0187"
    added = migration.op.add_column.call_args.args
    assert added[0] == "agent_pipeline_runs"
    assert added[1].name == "requested_by" and added[1].nullable is True
    migration.op.create_foreign_key.assert_called_once_with(
        "fk_agent_pipeline_runs_requested_by_users",
        "agent_pipeline_runs",
        "users",
        ["requested_by"],
        ["id"],
        ondelete="SET NULL",
    )

    migration.downgrade()

    migration.op.drop_constraint.assert_called_once_with(
        "fk_agent_pipeline_runs_requested_by_users",
        "agent_pipeline_runs",
        type_="foreignkey",
    )
    migration.op.drop_column.assert_called_once_with(
        "agent_pipeline_runs", "requested_by"
    )


def test_pipeline_model_matches_the_requested_by_foreign_key():
    column = AgentPipelineRun.__table__.c.requested_by

    assert column.nullable is True
    (foreign_key,) = column.foreign_keys
    assert foreign_key.target_fullname == "users.id"
    assert foreign_key.ondelete == "SET NULL"
