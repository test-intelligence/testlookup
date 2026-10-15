"""Migration 0209 (AGT-16): the ``aiq_gap_refinement`` row, ON for every install.

Pins the shape without a database: one row with its exact description, ON
unless ``AIQ_GAP_REFINEMENT_ENABLED`` is set (then its parsed value), the
idempotent INSERT and the untouched-row DELETE, and that 0209 is the single
Alembic head. The Postgres behaviour runs in
``tests/integration/test_migration_0207_0209_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "0209_seed_aiq_gap_refinement_flag.py"
DESCRIPTION = (
    "Deterministic gap-detection and report-refinement stages of the deep pipeline "
    "(no LLM, no egress); adds the \"Analysis coverage\" line to decision reports."
)


def _module():
    spec = importlib.util.spec_from_file_location("migration_0209", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _revisions() -> dict[str, tuple[str, ...]]:
    """revision -> its down revisions, read from every migration file's AST."""
    graph: dict[str, tuple[str, ...]] = {}
    for path in VERSIONS.glob("*.py"):
        values: dict[str, object] = {}
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                    values[target.id] = ast.literal_eval(node.value)
        if "revision" not in values:
            continue
        down = values.get("down_revision")
        downs = tuple(down) if isinstance(down, (tuple, list)) else ((down,) if down else ())
        graph[str(values["revision"])] = tuple(str(d) for d in downs)
    return graph


def test_it_follows_0208_and_the_chain_keeps_one_head():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0209", "0208")
    assert "Revision ID: 0209" in migration.__doc__ and "Revises: 0208" in migration.__doc__
    graph = _revisions()
    referenced = {d for downs in graph.values() for d in downs}
    heads = sorted(set(graph) - referenced)
    # One head, at or after 0209 (later waves chain onto it; W05 adds 0210-0212).
    assert len(heads) == 1 and heads[0] >= "0209", heads
    # The W04 chain is linear: 0206 -> 0207 -> 0208 -> 0209.
    assert graph["0207"] == ("0206",) and graph["0208"] == ("0207",) and graph["0209"] == ("0208",)


def test_the_row_and_its_legacy_variable():
    from app.services.feature_flags import LEGACY_ENV_VAR_MAP

    migration = _module()
    assert migration._FLAGS == (("aiq_gap_refinement", DESCRIPTION, "AIQ_GAP_REFINEMENT_ENABLED"),)
    assert DESCRIPTION.isascii()
    # G-B adds the map entry (contract 1.5); until then this documents the expectation.
    if "aiq_gap_refinement" in LEGACY_ENV_VAR_MAP:
        assert LEGACY_ENV_VAR_MAP["aiq_gap_refinement"] == migration.LEGACY_ENV


def test_on_for_every_install_when_the_variable_is_unset(monkeypatch):
    monkeypatch.delenv("AIQ_GAP_REFINEMENT_ENABLED", raising=False)
    migration = _module()
    assert migration.seed_enabled() is True
    assert migration.insert_params() == [
        {"key": "aiq_gap_refinement", "description": DESCRIPTION, "enabled": True}
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("1", True), (" ON ", True), ("yes", True),
     ("false", False), ("0", False), ("off", False), ("", False)],
)
def test_a_set_variable_wins(monkeypatch, value, expected):
    monkeypatch.setenv("AIQ_GAP_REFINEMENT_ENABLED", value)
    migration = _module()
    assert migration.seed_enabled() is expected
    assert migration.insert_params()[0]["enabled"] is expected


def test_the_insert_is_idempotent_and_needs_no_fresh_install_predicate():
    migration = _module()
    assert migration.INSERT_SQL == (
        "INSERT INTO feature_flags "
        "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
        "SELECT gen_random_uuid(), :key, :description, CAST(:enabled AS boolean), "
        "100, now(), now() "
        "ON CONFLICT (key) DO NOTHING"
    )
    assert "users" not in migration.INSERT_SQL


def test_upgrade_and_downgrade(monkeypatch):
    monkeypatch.delenv("AIQ_GAP_REFINEMENT_ENABLED", raising=False)
    migration = _module()
    migration.op = MagicMock()
    migration.upgrade()
    [insert] = migration.op.execute.call_args_list
    clause = insert.args[0]
    assert str(clause) == migration.INSERT_SQL
    assert {k: b.value for k, b in clause._bindparams.items()} == {
        "key": "aiq_gap_refinement", "description": DESCRIPTION, "enabled": True,
    }
    migration.op = MagicMock()
    migration.downgrade()
    [delete] = migration.op.execute.call_args_list
    clause = delete.args[0]
    assert str(clause) == (
        "DELETE FROM feature_flags WHERE key = :key AND description = :description "
        "AND created_at = updated_at AND enabled_projects IS NULL AND enabled_roles IS NULL "
        "AND rollout_percent = 100"
    )
    assert {k: b.value for k, b in clause._bindparams.items()} == {
        "key": "aiq_gap_refinement", "description": DESCRIPTION,
    }
