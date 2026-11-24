"""Migration 0260 (product finding D-53: who proposed a quarantine) -- shape only.

* the chain 0259 -> 0260, with 0260 the only head (at the time of writing); no
  application import;
* the upgrade adds ONE nullable UUID column, ``proposed_by_user_id``, FK
  ``users.id`` ON DELETE SET NULL (the ``approved_by_user_id`` twin); the
  downgrade drops exactly it;
* the ORM ``FlakyQuarantineRequest.proposed_by_user_id`` is in step with the DDL.

No database: a nullable column with no default is metadata-only on PostgreSQL.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy.dialects.postgresql import UUID

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0260 = VERSIONS / "0260_flaky_quarantine_proposed_by.py"
TABLE = "flaky_quarantine_requests"
COLUMN = "proposed_by_user_id"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, M0260)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.op = MagicMock()
    return mod


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
            down = values.get("down_revision")
            out[str(values["revision"])] = str(down) if down is not None else None
    return out


def test_the_chain_is_linear_with_0260_on_0259():
    revisions = _revisions()
    assert revisions["0260"] == "0259"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0259") == 1, "0259 has two children: the chain forked"
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0260"
    mod = _load("m0260_rev")
    assert "Revision ID: 0260" in mod.__doc__ and "Revises: 0259" in mod.__doc__


def test_the_migration_imports_no_application_code():
    tree = ast.parse(M0260.read_text(encoding="utf-8"))
    modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    modules += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [m for m in modules if m == "app" or m.startswith("app.")], modules


def test_upgrade_adds_one_nullable_set_null_user_fk():
    mod = _load("m0260_up")
    mod.upgrade()
    (call,) = mod.op.method_calls
    assert call[0] == "add_column"
    table, column = call.args
    assert table == TABLE and column.name == COLUMN
    assert column.nullable is True and column.server_default is None
    assert isinstance(column.type, UUID)
    (fk,) = column.foreign_keys
    assert fk.target_fullname == "users.id" and fk.ondelete == "SET NULL"


def test_downgrade_drops_exactly_the_column():
    mod = _load("m0260_down")
    mod.downgrade()
    assert [(c[0], c.args) for c in mod.op.method_calls] == [("drop_column", (TABLE, COLUMN))]


def test_the_orm_column_matches_the_ddl():
    from app.models.postgres import FlakyQuarantineRequest

    column = FlakyQuarantineRequest.__table__.c[COLUMN]
    approved = FlakyQuarantineRequest.__table__.c["approved_by_user_id"]
    assert column.nullable is True
    assert type(column.type) is type(approved.type)
    (fk,) = column.foreign_keys
    assert fk.target_fullname == "users.id" and fk.ondelete == "SET NULL"
