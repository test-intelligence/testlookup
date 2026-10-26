"""Migration 0232 (W17 ADM-07 / ADM-04): shape only, no database.

* ``settings_audit_log.reason`` ``TEXT NULL`` (the "why" of a change);
* ``projects.archived_at`` ``TIMESTAMPTZ NULL`` and ``projects.archived_by_user_id``
  ``UUID NULL`` with ``fk_projects_archived_by`` -> ``users.id`` ``ON DELETE SET NULL``.

Pinned here: the revision and docstring; the chain 0231 -> 0232, linear, 0232
the only head (earlier migration tests assert ``heads[0] >= …`` and keep
passing); no application import; no index build (nothing for the
concurrent-index guard to judge); the upgrade's operations in order; the
downgrade is its exact reverse; the ORM declares the same names, types,
nullability and foreign key. The PostgreSQL behaviour runs in
``tests/integration/test_project_restore_postgres.py`` and
``tests/integration/test_feature_flag_reason_postgres.py`` (a database at head).
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import sqlalchemy as sa

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0232 = VERSIONS / "0232_admin_reason_and_archive_stamp.py"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, M0232)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(step: str) -> MagicMock:
    module = _load(f"m0232_{step}")
    module.op = MagicMock()
    getattr(module, step)()
    return module.op


def _revisions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for path in VERSIONS.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        values: dict[str, object] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in {"revision", "down_revision"} and isinstance(node.value, ast.Constant):
                    values[node.targets[0].id] = node.value.value
        if "revision" in values:
            out[str(values["revision"])] = values.get("down_revision")  # type: ignore[assignment]
    return out


def test_the_revision_and_its_docstring():
    m = _load("m0232")
    assert (m.revision, m.down_revision) == ("0232", "0231")
    assert "Revision ID: 0232" in m.__doc__ and "Revises: 0231" in m.__doc__
    assert "append-only" in m.__doc__


def test_the_chain_is_linear_with_0232_the_only_head():
    revisions = _revisions()
    assert revisions["0232"] == "0231"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0231") == 1, "nothing else forks off 0231"
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0232"


def test_it_imports_no_application_code_and_builds_no_index():
    tree = ast.parse(M0232.read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names
    }
    assert not any(name.startswith("app") for name in imported), imported
    op = _run("upgrade")
    assert not op.create_index.called


def test_the_upgrade_adds_three_nullable_columns_and_the_foreign_key():
    op = _run("upgrade")
    added = [(c.args[0], c.args[1].name, type(c.args[1].type), c.args[1].nullable) for c in op.add_column.call_args_list]
    assert added == [
        ("settings_audit_log", "reason", sa.Text, True),
        ("projects", "archived_at", sa.DateTime, True),
        ("projects", "archived_by_user_id", sa.dialects.postgresql.UUID, True),
    ]
    for call in op.add_column.call_args_list:
        assert call.args[1].server_default is None  # no backfill: old rows read NULL
    assert op.add_column.call_args_list[1].args[1].type.timezone is True
    op.create_foreign_key.assert_called_once_with(
        "fk_projects_archived_by", "projects", "users", ["archived_by_user_id"], ["id"], ondelete="SET NULL",
    )


def test_the_downgrade_is_the_exact_reverse():
    op = _run("downgrade")
    op.drop_constraint.assert_called_once_with("fk_projects_archived_by", "projects", type_="foreignkey")
    assert [c.args for c in op.drop_column.call_args_list] == [
        ("projects", "archived_by_user_id"), ("projects", "archived_at"), ("settings_audit_log", "reason"),
    ]
    names = [c[0] for c in op.method_calls]
    assert names.index("drop_constraint") < names.index("drop_column")


def test_the_orm_declares_the_same_columns():
    from app.models.postgres import Project, SettingsAuditLog

    reason = SettingsAuditLog.__table__.c.reason
    assert isinstance(reason.type, sa.Text) and reason.nullable and reason.server_default is None
    at = Project.__table__.c.archived_at
    assert isinstance(at.type, sa.DateTime) and at.type.timezone and at.nullable
    by = Project.__table__.c.archived_by_user_id
    assert by.nullable
    (fk,) = by.foreign_keys
    assert fk.target_fullname == "users.id" and fk.ondelete == "SET NULL" and fk.name == "fk_projects_archived_by"
