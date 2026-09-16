"""D3 schema contract for durable Investigator trigger attribution."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

from app.models.postgres import AgentInvestigation

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0189_investigation_requested_by.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("migration_0189", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_adds_nullable_set_null_attribution_and_reverses_it():
    migration = _module()
    migration.op = MagicMock()

    migration.upgrade()

    assert migration.down_revision == "0188"
    added = migration.op.add_column.call_args.args
    assert added[0] == "agent_investigations"
    assert added[1].name == "requested_by" and added[1].nullable is True
    migration.op.create_foreign_key.assert_called_once_with(
        "fk_agent_investigations_requested_by_users",
        "agent_investigations",
        "users",
        ["requested_by"],
        ["id"],
        ondelete="SET NULL",
    )

    migration.downgrade()

    migration.op.drop_constraint.assert_called_once_with(
        "fk_agent_investigations_requested_by_users",
        "agent_investigations",
        type_="foreignkey",
    )
    migration.op.drop_column.assert_called_once_with(
        "agent_investigations", "requested_by"
    )


def test_investigation_model_matches_the_requested_by_foreign_key():
    column = AgentInvestigation.__table__.c.requested_by

    assert column.nullable is True
    (foreign_key,) = column.foreign_keys
    assert foreign_key.target_fullname == "users.id"
    assert foreign_key.ondelete == "SET NULL"
