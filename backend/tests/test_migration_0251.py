"""Migration 0251 (W25d HYG-04 phase 2): the cutover switch is seeded ON, everywhere.

Pins the shape without a database (the ``test_migration_0208_agent_stage_flags.py``
shape): the chain 0250 -> 0251 with 0251 the only head; no application import;
ONE row, ``suite_membership_legacy_writes``, with the contract's description
(§1.2); the INSERT seeds it ON (``enabled_global = true``, rollout 100) on
every install -- no fresh-install clause, no environment read (D-W25d-2) --
and leaves an existing row alone (``ON CONFLICT (key) DO NOTHING``); the
downgrade deletes the row only while it still equals the untouched seed (a
cut-over row survives); the feature-flag catalogue guard reads the key from
the migration and the code, and the operator catalogue lists it. The Postgres
round trip of 0251 runs in G-A's
``tests/integration/test_suite_membership_cutover_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "0251_suite_membership_legacy_writes_flag.py"
KEY = "suite_membership_legacy_writes"
#: Contract §1.2, verbatim.
DESCRIPTION = (
    "ON: the legacy suite-membership tables are still written and read (before the HYG-04 "
    "cutover). Turn OFF only from Settings > Storage when the cutover card says it is ready."
)
INSERT_SQL = (
    "INSERT INTO feature_flags "
    "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
    "SELECT gen_random_uuid(), :key, :description, true, 100, now(), now() "
    "ON CONFLICT (key) DO NOTHING"
)
DELETE_SQL = (
    "DELETE FROM feature_flags WHERE key = :key AND description = :description "
    "AND created_at = updated_at AND enabled_projects IS NULL AND enabled_roles IS NULL "
    "AND rollout_percent = 100"
)


def _module():
    spec = importlib.util.spec_from_file_location("migration_0251", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


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


def _bound(clause) -> dict[str, object]:
    return {name: bp.value for name, bp in clause._bindparams.items()}


# ── the chain and the hygiene ────────────────────────────────────────────────


def test_it_follows_0250():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0251", "0250")
    assert "Revision ID: 0251" in migration.__doc__ and "Revises: 0250" in migration.__doc__


def test_the_chain_is_linear_with_0251_the_only_head():
    revisions = _revisions()
    assert revisions["0251"] == "0250"
    assert "0250" in revisions, "0250_drop_dead_tables (W25d G-B) is missing: 0251 chains onto it"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0250") == 1  # no fork at 0250
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0251"


def test_the_migration_imports_no_application_code_and_reads_no_environment():
    tree = ast.parse(_source())
    modules = [
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    ] + [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [m for m in modules if m == "app" or m.startswith("app.")], modules
    # Seeded ON on every install (D-W25d-2): no legacy env fallback, no fresh-install predicate.
    assert "os" not in modules, modules
    assert "FRESH_INSTALL" not in _source()
    assert "SELECT 1 FROM users" not in _source()


# ── the row ──────────────────────────────────────────────────────────────────


def test_one_row_the_cutover_switch_with_the_contracts_description():
    migration = _module()
    assert migration._FLAGS == ((KEY, DESCRIPTION),)
    assert DESCRIPTION.isascii()


def test_the_key_is_the_cutover_modules_flag():
    from app.services import suite_membership_cutover

    assert suite_membership_cutover.FEATURE_FLAG == KEY


def test_the_insert_seeds_it_on_everywhere_and_keeps_an_existing_row():
    migration = _module()
    assert migration.INSERT_SQL == INSERT_SQL
    assert migration.INSERT_SQL.endswith("ON CONFLICT (key) DO NOTHING")
    assert ":key, :description, true, 100, now(), now()" in migration.INSERT_SQL


def test_upgrade_inserts_the_row_with_its_bound_key_and_description():
    migration = _module()
    migration.op = MagicMock()
    migration.upgrade()
    calls = migration.op.execute.call_args_list
    assert len(calls) == 1
    clause = calls[0].args[0]
    assert str(clause) == INSERT_SQL
    assert _bound(clause) == {"key": KEY, "description": DESCRIPTION}


def test_downgrade_deletes_only_the_untouched_seed_row():
    """A cutover stamps ``updated_at`` (and flips ``enabled_global``): that row survives a downgrade."""
    migration = _module()
    assert migration.DELETE_SQL == DELETE_SQL
    migration.op = MagicMock()
    migration.downgrade()
    calls = migration.op.execute.call_args_list
    assert len(calls) == 1
    clause = calls[0].args[0]
    assert str(clause) == DELETE_SQL
    assert _bound(clause) == {"key": KEY, "description": DESCRIPTION}


# ── the catalogue guard ──────────────────────────────────────────────────────


def test_the_catalogue_guard_reads_the_key_from_the_migration_and_the_code():
    from tests.regression import test_feature_flag_catalogue_is_complete as catalogue

    assert KEY in catalogue._keys_in_migration(_source())
    assert KEY in catalogue.seeded_flag_keys()
    assert KEY in catalogue.flag_keys_read_by_code()  # FEATURE_FLAG = "…" in the cutover module


def test_the_operator_catalogue_lists_the_key():
    from tests.regression import test_feature_flag_catalogue_is_complete as catalogue

    assert f"`{KEY}`" in catalogue._catalogue()
