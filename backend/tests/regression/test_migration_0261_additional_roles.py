"""Migration 0261 (enterprise roles E00): extra job roles, and a downgrade older code can read.

Upgrade adds a nullable JSONB ``additional_roles`` to users, project_members and user_invitations. Downgrade
maps every new role value back to VIEWER in every place older code reads a role through its five-value enum --
including SCIM group mappings and the per-project entries of a pending invitation -- then drops the columns.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0261_role_additional_roles.py"
NEW_ROLES = ["RELEASE_MANAGER", "PROJECT_MANAGER", "PRODUCT_MANAGER", "DEVELOPER", "AGENT"]


def _load():
    spec = importlib.util.spec_from_file_location("migration_0261", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0261_follows_0260():
    module = _load()
    assert (module.revision, module.down_revision) == ("0261", "0260")


def test_upgrade_adds_a_nullable_column_to_three_tables(monkeypatch):
    from alembic import op

    added: list = []
    monkeypatch.setattr(op, "add_column", lambda table, column: added.append((table, column)), raising=False)
    _load().upgrade()
    assert [table for table, _ in added] == ["users", "project_members", "user_invitations"]
    assert all(col.name == "additional_roles" and col.nullable for _, col in added)


def test_downgrade_maps_new_roles_to_viewer_everywhere_then_drops_the_columns(monkeypatch):
    from alembic import op

    sent: list = []
    dropped: list = []
    monkeypatch.setattr(op, "execute", sent.append, raising=False)
    monkeypatch.setattr(op, "drop_column", lambda table, column: dropped.append((table, column)), raising=False)
    _load().downgrade()
    sql = [str(stmt) for stmt in sent]
    for table, column in (("users", "role"), ("project_members", "role"), ("user_invitations", "role"),
                          ("sso_configurations", "default_role"), ("scim_group_mappings", "project_role")):
        assert any(f"UPDATE {table} SET {column} = 'VIEWER'" in s for s in sql), (table, column)
    invites = next(s for s in sql if "project_memberships" in s)
    assert "jsonb_set" in invites and "- 'additional_roles'" in invites
    for stmt in sent:
        assert stmt.compile().params.get("new_roles") == NEW_ROLES
    assert dropped == [(t, "additional_roles") for t in ("users", "project_members", "user_invitations")]
