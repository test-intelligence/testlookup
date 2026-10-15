"""Migration 0212 (REL-10 + REL-04): two release flags start ON for a new install, only for one.

Pins the shape without a database: each flip uses 0201's fresh-install guard
verbatim and only moves its seed's OFF; the downgrade is the mirror; the keys
are visible to the feature-flag catalogue guard; and the chain 0209 -> 0212 is
linear with one head. The PostgreSQL behaviour runs in
``tests/integration/test_migration_0211_0212_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from unittest.mock import MagicMock

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "0212_release_flags_on_for_new_installs.py"
FRESH_INSTALL = "NOT EXISTS (SELECT 1 FROM users) AND NOT EXISTS (SELECT 1 FROM projects)"
KEYS = ("release_phase_gate_enforcement", "release_compliance_pack")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _module():
    return _load(MIGRATION, "migration_0212")


def test_it_follows_0211():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0212", "0211")
    assert "Revision ID: 0212" in migration.__doc__ and "Revises: 0211" in migration.__doc__


def test_the_fresh_install_predicate_is_0201s_verbatim():
    migration = _module()
    m0201 = _load(VERSIONS / "0201_manual_upload_on_for_new_installs.py", "migration_0201")
    assert migration.FRESH_INSTALL == m0201.FRESH_INSTALL == FRESH_INSTALL


def test_upgrade_flips_only_each_seeded_off_on_an_unused_database():
    migration = _module()
    assert migration._FLAGS == KEYS
    assert migration.UPGRADE_SQL == tuple(
        "UPDATE feature_flags SET enabled_global = true, updated_at = now() "
        f"WHERE key = '{key}' AND enabled_global = false AND " + FRESH_INSTALL
        for key in KEYS
    )


def test_downgrade_is_the_mirror_on_the_same_databases():
    migration = _module()
    assert migration.DOWNGRADE_SQL == tuple(
        "UPDATE feature_flags SET enabled_global = false, updated_at = now() "
        f"WHERE key = '{key}' AND enabled_global = true AND " + FRESH_INSTALL
        for key in KEYS
    )


def test_upgrade_and_downgrade_execute_exactly_their_statements():
    migration = _module()
    migration.op = MagicMock()
    migration.upgrade()
    assert [str(c.args[0]) for c in migration.op.execute.call_args_list] == list(migration.UPGRADE_SQL)
    migration.op = MagicMock()
    migration.downgrade()
    assert [str(c.args[0]) for c in migration.op.execute.call_args_list] == list(migration.DOWNGRADE_SQL)
    assert not migration.op.add_column.called and not migration.op.drop_column.called


def test_the_catalogue_guard_sees_both_keys():
    from tests.regression.test_feature_flag_catalogue_is_complete import _keys_in_migration

    assert set(KEYS) <= _keys_in_migration(MIGRATION.read_text(encoding="utf-8"))


def _revisions() -> dict[str, str | None]:
    """revision -> down_revision for every migration file, read from the AST."""
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


def test_the_chain_is_linear_with_0212_the_only_head():
    revisions = _revisions()
    for rev, parent in (("0210", "0209"), ("0211", "0210"), ("0212", "0211")):
        assert revisions[rev] == parent
    parents = {p for p in revisions.values() if p}
    heads = sorted(r for r in revisions if r not in parents)
    # Another wave may chain onto 0212 later; what must never happen is a fork
    # at or below it.
    assert len(heads) == 1, heads
    assert re.fullmatch(r"\d{4}", heads[0]) and heads[0] >= "0212"
