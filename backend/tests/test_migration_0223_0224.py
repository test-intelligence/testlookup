"""Migrations 0223 (W10 INT-04) and 0224 (W10 INT-07): shape only, no database.

0223 gives both integration-health tables a ``scope_key`` and swaps
``integration_health_checks``' primary key to ``(provider, scope_key)``
without a blocking build: the unique index is built CONCURRENTLY inside
``autocommit_block()`` and the key ADOPTS it (``PRIMARY KEY USING INDEX``); the
history index is built CONCURRENTLY. 0224 adds the four circuit-breaker columns
and turns ``outbound_webhooks`` ON for a new install only (0201's rule).

Pinned here: the chain 0222 -> 0223 -> 0224 with 0224 the only head; each
docstring's ``Revision ID`` / ``Revises``; no application import; the
concurrent-index guard's own scanner sees exactly the expected builds and
drops, all concurrent or adopted; ``_FLAGS`` visible to the feature-flag
catalogue guard; the fresh-install predicate is 0201's verbatim; the ORM
declares the same names, types, nullability and server defaults; each
downgrade undoes exactly its upgrade. The PostgreSQL behaviour runs in
``tests/integration/test_migration_0223_0224_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import sqlalchemy as sa

from tests.regression.test_migration_index_builds_are_concurrent import (
    _blocking,
    _concurrent_outside_autocommit,
    _operations,
)

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0223 = VERSIONS / "0223_integration_health_scope.py"
M0224 = VERSIONS / "0224_webhook_circuit_breaker.py"
FRESH_INSTALL = "NOT EXISTS (SELECT 1 FROM users) AND NOT EXISTS (SELECT 1 FROM projects)"
WEBHOOK_COLUMNS = ["consecutive_failures", "breaker_threshold", "disabled_at", "disabled_reason"]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _offline_op() -> MagicMock:
    """An ``op`` whose context renders SQL (no catalog to read)."""
    op = MagicMock()
    op.get_context.return_value.as_sql = True
    return op


def _run(path: Path, step: str, op: MagicMock | None = None):
    module = _load(path, f"m_{path.name[:4]}_{step}")
    module.op = op or _offline_op()
    getattr(module, step)()
    return module, module.op


def _executed(op: MagicMock) -> list[str]:
    return [str(call.args[0]) for call in op.execute.call_args_list]


# ── Revisions, docstrings, imports, chain ───────────────────────────────────


def test_the_revisions_and_their_docstrings():
    m0223, m0224 = _load(M0223, "m0223"), _load(M0224, "m0224")
    assert (m0223.revision, m0223.down_revision) == ("0223", "0222")
    assert (m0224.revision, m0224.down_revision) == ("0224", "0223")
    assert "Revision ID: 0223" in m0223.__doc__ and "Revises: 0222" in m0223.__doc__
    assert "Revision ID: 0224" in m0224.__doc__ and "Revises: 0223" in m0224.__doc__


def test_neither_imports_application_code():
    for path in (M0223, M0224):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not any(name.startswith("app") for name in imported), (path.name, imported)


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


def test_the_chain_is_linear_with_0224_the_only_head():
    revisions = _revisions()
    assert revisions["0223"] == "0222" and revisions["0224"] == "0223"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0222") == 1 and parents.count("0223") == 1   # no fork at or below
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0224"


# ── 0223: the concurrent route, as the repo guard reads it ─────────────────


def test_0223_builds_and_drops_only_concurrently_or_by_adoption():
    source = M0223.read_text(encoding="utf-8")
    assert _blocking(source) == []
    assert _concurrent_outside_autocommit(source) == []
    seen = sorted((op.kind, op.table, op.concurrent) for op in _operations(source))
    assert seen == sorted([
        ("index", "integration_health_checks", True),     # uq (provider, scope_key)
        ("index", "integration_probe_results", True),     # ix_ipr_provider_scope_time
        ("index", "integration_health_checks", True),     # downgrade: (provider)
        ("drop", "integration_probe_results", True),      # downgrade: the history index
        ("drop", "<unknown>", True),                       # an INVALID leftover, concurrently
    ])


def test_0223_upgrade_statements_in_order():
    module, op = _run(M0223, "upgrade")
    added = [(call.args[0], call.args[1].name, call.kwargs) for call in op.add_column.call_args_list]
    assert added == [
        ("integration_health_checks", "scope_key", {"if_not_exists": True}),
        ("integration_probe_results", "scope_key", {"if_not_exists": True}),
    ]
    for _, column in (call.args for call in op.add_column.call_args_list):
        assert isinstance(column.type, sa.String) and column.type.length == 64
        assert column.nullable is False and column.server_default.arg == ""
    assert _executed(op) == [
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_integration_health_checks_provider_scope "
        "ON integration_health_checks (provider, scope_key)",
        "ALTER TABLE integration_health_checks DROP CONSTRAINT integration_health_checks_pkey, "
        "ADD CONSTRAINT integration_health_checks_pkey PRIMARY KEY USING INDEX "
        "uq_integration_health_checks_provider_scope",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ipr_provider_scope_time "
        "ON integration_probe_results (provider, scope_key, checked_at)",
    ]
    assert op.get_context.return_value.autocommit_block.call_count == 2
    assert not op.get_bind.called          # offline: the catalog is not read
    assert not op.drop_column.called


def test_0223_downgrade_undoes_exactly_the_upgrade():
    module, op = _run(M0223, "downgrade")
    assert _executed(op) == [
        "DELETE FROM integration_health_checks WHERE scope_key <> ''",
        "DELETE FROM integration_probe_results WHERE scope_key <> ''",
        "DROP INDEX CONCURRENTLY IF EXISTS ix_ipr_provider_scope_time",
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS integration_health_checks_provider_key "
        "ON integration_health_checks (provider)",
        "ALTER TABLE integration_health_checks DROP CONSTRAINT integration_health_checks_pkey, "
        "ADD CONSTRAINT integration_health_checks_pkey PRIMARY KEY USING INDEX "
        "integration_health_checks_provider_key",
    ]
    assert [call.args for call in op.drop_column.call_args_list] == [
        ("integration_probe_results", "scope_key"),
        ("integration_health_checks", "scope_key"),
    ]
    assert op.get_context.return_value.autocommit_block.call_count == 2
    assert not op.add_column.called


class _Catalog:
    """A bind answering the two catalog reads 0223 makes online."""

    def __init__(self, pkey="integration_health_checks_pkey", invalid=()):
        self.pkey = pkey
        self.invalid = set(invalid)
        self.asked: list[tuple[str, dict]] = []

    def execute(self, statement, params):
        self.asked.append((str(statement), params))
        result = MagicMock()
        if "pg_constraint" in str(statement):
            result.scalar_one_or_none.return_value = self.pkey
        else:
            result.scalar_one_or_none.return_value = True if params["index"] in self.invalid else None
        return result


def _online_op(catalog: _Catalog) -> MagicMock:
    op = MagicMock()
    op.get_context.return_value.as_sql = False
    op.get_bind.return_value = catalog
    return op


def test_0223_online_drops_the_key_the_catalog_names():
    catalog = _Catalog(pkey='health "old" pk')
    _, op = _run(M0223, "upgrade", _online_op(catalog))
    alter = _executed(op)[1]
    assert alter.startswith('ALTER TABLE integration_health_checks DROP CONSTRAINT "health ""old"" pk", ')
    assert alter.endswith("ADD CONSTRAINT integration_health_checks_pkey PRIMARY KEY USING INDEX "
                          "uq_integration_health_checks_provider_scope")
    assert {params["table"] for sql, params in catalog.asked if "pg_constraint" in sql} == {
        "integration_health_checks",
    }


def test_0223_online_drops_an_invalid_leftover_first_and_keeps_a_valid_one():
    catalog = _Catalog(invalid={"uq_integration_health_checks_provider_scope"})
    _, op = _run(M0223, "upgrade", _online_op(catalog))
    executed = _executed(op)
    assert executed[0] == "DROP INDEX CONCURRENTLY IF EXISTS uq_integration_health_checks_provider_scope"
    assert executed[1].startswith("CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_integration_health_checks")
    assert not any("DROP INDEX" in s and "ix_ipr_provider_scope_time" in s for s in executed)


def test_0223_online_without_a_primary_key_stops():
    import pytest

    with pytest.raises(RuntimeError, match="no primary key"):
        _run(M0223, "upgrade", _online_op(_Catalog(pkey=None)))


def test_0223_matches_the_orm():
    from app.models.postgres import IntegrationHealthCheck, IntegrationProbeResult

    health = IntegrationHealthCheck.__table__
    history = IntegrationProbeResult.__table__
    for table in (health, history):
        column = table.c["scope_key"]
        assert isinstance(column.type, sa.String) and column.type.length == 64
        assert column.nullable is False
        assert column.server_default.arg == ""
        assert column.default.arg == ""
    assert [c.name for c in health.primary_key.columns] == ["provider", "scope_key"]
    (index,) = [i for i in history.indexes if i.name == "ix_ipr_provider_scope_time"]
    assert [c.name for c in index.columns] == ["provider", "scope_key", "checked_at"]
    assert any(i.name == "ix_ipr_provider_time" for i in history.indexes)   # kept


# ── 0224 ────────────────────────────────────────────────────────────────────


def test_0224_flips_only_an_unused_database_by_0201s_rule():
    m0224 = _load(M0224, "m0224_flag")
    m0201 = _load(VERSIONS / "0201_manual_upload_on_for_new_installs.py", "m0201")
    assert m0224.FRESH_INSTALL == m0201.FRESH_INSTALL == FRESH_INSTALL
    assert m0224._FLAGS == ("outbound_webhooks",)
    assert m0224.UPGRADE_SQL == (
        "UPDATE feature_flags SET enabled_global = true, updated_at = now() "
        "WHERE key = 'outbound_webhooks' AND enabled_global = false AND " + FRESH_INSTALL
    )
    assert m0224.DOWNGRADE_SQL == (
        "UPDATE feature_flags SET enabled_global = false, updated_at = now() "
        "WHERE key = 'outbound_webhooks' AND enabled_global = true AND " + FRESH_INSTALL
    )


def test_0224_flag_is_visible_to_the_catalogue_guard():
    from tests.regression.test_feature_flag_catalogue_is_complete import _keys_in_migration

    assert "outbound_webhooks" in _keys_in_migration(M0224.read_text(encoding="utf-8"))


def test_0224_builds_no_index():
    source = M0224.read_text(encoding="utf-8")
    assert _operations(source) == []


def test_0224_upgrade_adds_the_four_columns_then_flips_the_flag():
    module, op = _run(M0224, "upgrade")
    added = {call.args[1].name: (call.args[0], call.args[1]) for call in op.add_column.call_args_list}
    assert list(added) == WEBHOOK_COLUMNS
    assert {table for table, _ in added.values()} == {"webhook_subscriptions"}
    failures, threshold = added["consecutive_failures"][1], added["breaker_threshold"][1]
    assert isinstance(failures.type, sa.Integer) and failures.nullable is False
    assert str(failures.server_default.arg) == "0"
    assert isinstance(threshold.type, sa.Integer) and threshold.nullable is False
    assert str(threshold.server_default.arg) == "10"
    disabled_at, reason = added["disabled_at"][1], added["disabled_reason"][1]
    assert isinstance(disabled_at.type, sa.DateTime) and disabled_at.type.timezone is True
    assert disabled_at.nullable is True and disabled_at.server_default is None
    assert isinstance(reason.type, sa.String) and reason.type.length == 40
    assert reason.nullable is True and reason.server_default is None
    assert _executed(op) == [module.UPGRADE_SQL]
    assert not op.create_check_constraint.called and not op.create_index.called


def test_0224_downgrade_is_the_mirror():
    module, op = _run(M0224, "downgrade")
    assert _executed(op) == [module.DOWNGRADE_SQL]
    assert [call.args for call in op.drop_column.call_args_list] == [
        ("webhook_subscriptions", name) for name in reversed(WEBHOOK_COLUMNS)
    ]
    assert not op.add_column.called


def test_0224_matches_the_orm():
    from app.models.postgres import WebhookSubscription

    _, op = _run(M0224, "upgrade")
    table = WebhookSubscription.__table__
    for call in op.add_column.call_args_list:
        migrated = call.args[1]
        declared = table.c[migrated.name]
        assert declared.nullable is migrated.nullable, migrated.name
        assert type(declared.type)._type_affinity is type(migrated.type)._type_affinity, migrated.name
        assert not declared.index and not declared.unique, migrated.name
    assert str(table.c["consecutive_failures"].server_default.arg) == "0"
    assert str(table.c["breaker_threshold"].server_default.arg) == "10"
    assert table.c["consecutive_failures"].default.arg == 0
    assert table.c["breaker_threshold"].default.arg == 10
    assert table.c["disabled_reason"].type.length == 40
    assert table.c["disabled_at"].type.timezone is True
