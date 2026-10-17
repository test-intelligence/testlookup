"""Migration 0221 (W09 NTF-04): ``digest_subscriptions.saved_view_name``.

Shape only (no database): the chain after 0220 and the linear chain up to
0221; the nullable ``String(255)`` column the ORM declares with the same name,
type and nullability; the backfill of rows bound to a view that still exists;
no index; a downgrade that drops exactly the column. The PostgreSQL behaviour
runs in ``tests/integration/test_migration_0219_0221_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import sqlalchemy as sa

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "0221_digest_saved_view_name.py"


def _module():
    spec = importlib.util.spec_from_file_location("migration_0221", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_follows_0220():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0221", "0220")
    assert "Revision ID: 0221" in migration.__doc__ and "Revises: 0220" in migration.__doc__


def test_upgrade_adds_the_nullable_column_then_backfills_bound_rows():
    migration = _module()
    migration.op = MagicMock()
    migration.op.get_bind.return_value.execute.return_value.rowcount = 2
    migration.upgrade()
    (table, column), _ = migration.op.add_column.call_args
    assert table == "digest_subscriptions"
    assert column.name == "saved_view_name" and column.nullable is True
    assert isinstance(column.type, sa.String) and column.type.length == 255
    assert column.server_default is None
    (statement,), _ = migration.op.get_bind.return_value.execute.call_args
    assert str(statement) == (
        "UPDATE digest_subscriptions d SET saved_view_name = v.name "
        "FROM saved_views v WHERE v.id = d.saved_view_id AND d.saved_view_name IS NULL"
    )
    assert not migration.op.create_index.called


def test_downgrade_drops_exactly_the_column():
    migration = _module()
    migration.op = MagicMock()
    migration.downgrade()
    migration.op.drop_column.assert_called_once_with("digest_subscriptions", "saved_view_name")
    assert not migration.op.execute.called


def test_the_orm_declares_the_same_column():
    from app.models.postgres import DigestSubscription

    column = DigestSubscription.__table__.c["saved_view_name"]
    assert column.nullable is True
    assert isinstance(column.type, sa.String) and column.type.length == 255
    assert not column.index and not column.unique


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


def test_the_w09_chain_is_linear_with_one_head():
    revisions = _revisions()
    for rev, parent in (("0219", "0218"), ("0220", "0219"), ("0221", "0220")):
        assert revisions[rev] == parent
    parents = {p for p in revisions.values() if p}
    heads = sorted(r for r in revisions if r not in parents)
    assert len(heads) == 1, heads
    assert heads[0] >= "0221"
