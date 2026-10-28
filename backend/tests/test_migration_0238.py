"""Migration 0238 (W21d ACT-02): shape only, no database.

0238 builds two partial indexes on EXISTING tables for ``GET
/api/v1/activity/mine``: ``ix_pae_actor_time`` (what one person did while
signed in, across projects, in keyset order) and ``ix_mtc_assignee`` (test
cases assigned to me). Both are built CONCURRENTLY inside
``autocommit_block()``, each preceded online by dropping an INVALID leftover of
its own name; the downgrade drops both CONCURRENTLY in reverse order.

Pinned here: the revision and docstring; no application import; the chain
0237 -> 0238, linear, 0238 the only head (every earlier migration test asserts
``heads[0] >= …`` and keeps passing); the concurrent-index guard's own scanner
sees exactly the two builds and the drops, all concurrent, none outside an
autocommit block; the upgrade's and downgrade's statements in order; the
online leftover handling; the ORM declares the same two indexes. The
PostgreSQL behaviour runs in ``tests/integration/test_migration_0238_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

from tests.regression.test_migration_index_builds_are_concurrent import (
    _blocking,
    _concurrent_outside_autocommit,
    _created_tables,
    _operations,
)

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0238 = VERSIONS / "0238_activity_mine_indexes.py"

BUILD_ACTOR = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_pae_actor_time "
    "ON project_activity_events (actor_id, occurred_at DESC, id DESC) "
    "WHERE actor_type = 'user'"
)
BUILD_ASSIGNEE = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_mtc_assignee "
    "ON managed_test_cases (assignee_id) "
    "WHERE assignee_id IS NOT NULL"
)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, M0238)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _offline_op() -> MagicMock:
    """An ``op`` whose context renders SQL (no catalog to read)."""
    op = MagicMock()
    op.get_context.return_value.as_sql = True
    return op


class _Catalog:
    """A bind answering 0238's one catalog read: is the index INVALID?"""

    def __init__(self, invalid=()):
        self.invalid = set(invalid)
        self.asked: list[str] = []

    def execute(self, statement, params):
        self.asked.append(params["index"])
        result = MagicMock()
        result.scalar_one_or_none.return_value = True if params["index"] in self.invalid else None
        return result


def _online_op(catalog: _Catalog) -> MagicMock:
    op = MagicMock()
    op.get_context.return_value.as_sql = False
    op.get_bind.return_value = catalog
    return op


def _run(step: str, op: MagicMock | None = None) -> MagicMock:
    module = _load(f"m0238_{step}")
    module.op = op or _offline_op()
    getattr(module, step)()
    return module.op


def _executed(op: MagicMock) -> list[str]:
    return [" ".join(str(call.args[0]).split()) for call in op.execute.call_args_list]


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


# ── Revision, docstring, imports, chain ─────────────────────────────────────


def test_the_revision_and_its_docstring():
    module = _load("m0238")
    assert (module.revision, module.down_revision) == ("0238", "0237")
    assert module.branch_labels is None and module.depends_on is None
    assert "Revision ID: 0238" in module.__doc__ and "Revises: 0237" in module.__doc__


def test_it_imports_no_application_code():
    tree = ast.parse(M0238.read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported == {"alembic", "sqlalchemy"}, imported


def test_the_chain_is_linear_with_0238_the_only_head():
    revisions = _revisions()
    assert revisions["0238"] == "0237"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0237") == 1   # no fork at 0237
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0238"


# ── The concurrent route, as the repo guard reads it ────────────────────────


def test_it_builds_and_drops_only_concurrently_inside_autocommit_blocks():
    source = M0238.read_text(encoding="utf-8")
    assert _created_tables(source) == set()        # both tables already exist
    assert _blocking(source) == []
    assert _concurrent_outside_autocommit(source) == []
    seen = sorted((op.kind, op.table, op.concurrent) for op in _operations(source))
    assert seen == sorted([
        ("index", "project_activity_events", True),   # ix_pae_actor_time
        ("index", "managed_test_cases", True),        # ix_mtc_assignee
        ("drop", "managed_test_cases", True),         # downgrade
        ("drop", "project_activity_events", True),    # downgrade
        ("drop", "<unknown>", True),                  # an INVALID leftover, concurrently
    ])


# ── Upgrade and downgrade, statement by statement ───────────────────────────


def test_the_upgrade_builds_both_indexes_in_their_own_autocommit_blocks():
    op = _run("upgrade")
    assert _executed(op) == [BUILD_ACTOR, BUILD_ASSIGNEE]
    assert op.get_context.return_value.autocommit_block.call_count == 2
    assert not op.get_bind.called          # offline: the catalog is not read
    for name in ("create_index", "add_column", "drop_index", "drop_column", "create_table"):
        assert not getattr(op, name).called, name


def test_the_downgrade_drops_exactly_the_two_in_reverse_order():
    op = _run("downgrade")
    assert _executed(op) == [
        "DROP INDEX CONCURRENTLY IF EXISTS ix_mtc_assignee",
        "DROP INDEX CONCURRENTLY IF EXISTS ix_pae_actor_time",
    ]
    assert op.get_context.return_value.autocommit_block.call_count == 2
    for name in ("create_index", "add_column", "drop_index", "drop_column", "drop_table"):
        assert not getattr(op, name).called, name


def test_online_an_invalid_leftover_is_dropped_first_and_a_valid_one_kept():
    catalog = _Catalog(invalid={"ix_pae_actor_time"})
    op = _run("upgrade", _online_op(catalog))
    assert catalog.asked == ["ix_pae_actor_time", "ix_mtc_assignee"]
    assert _executed(op) == [
        "DROP INDEX CONCURRENTLY IF EXISTS ix_pae_actor_time",
        BUILD_ACTOR,
        BUILD_ASSIGNEE,      # ix_mtc_assignee is valid (or absent): not dropped
    ]
    # the leftover drop runs in an autocommit block of its own
    assert op.get_context.return_value.autocommit_block.call_count == 3


def test_online_with_no_leftover_only_builds():
    op = _run("upgrade", _online_op(_Catalog()))
    assert _executed(op) == [BUILD_ACTOR, BUILD_ASSIGNEE]


# ── The ORM declares the same two indexes ───────────────────────────────────


def _index(model, name: str):
    (index,) = [i for i in model.__table__.indexes if i.name == name]
    return index


def _expressions(index) -> list[str]:
    return [" ".join(str(e).split()) for e in index.expressions]


def test_the_orm_declares_the_actor_index():
    from app.models.postgres import ProjectActivityEvent

    index = _index(ProjectActivityEvent, "ix_pae_actor_time")
    assert _expressions(index) == ["project_activity_events.actor_id", "occurred_at DESC", "id DESC"]
    assert str(index.dialect_options["postgresql"]["where"]) == "actor_type = 'user'"
    assert not index.unique


def test_the_orm_declares_the_assignee_index():
    from app.models.postgres import ManagedTestCase

    index = _index(ManagedTestCase, "ix_mtc_assignee")
    assert _expressions(index) == ["managed_test_cases.assignee_id"]
    assert str(index.dialect_options["postgresql"]["where"]) == "assignee_id IS NOT NULL"
    assert not index.unique


def test_both_indexes_still_build_under_sqlite():
    """Three suites build project_activity_events with SQLite ``create_all``:
    the predicate is PostgreSQL-only, ``DESC`` is valid everywhere."""
    import sqlalchemy as sa
    from sqlalchemy.schema import CreateIndex

    from app.models.postgres import ManagedTestCase, ProjectActivityEvent

    dialect = sa.create_engine("sqlite://").dialect
    rendered = {
        name: " ".join(str(CreateIndex(_index(model, name)).compile(dialect=dialect)).split())
        for model, name in ((ProjectActivityEvent, "ix_pae_actor_time"), (ManagedTestCase, "ix_mtc_assignee"))
    }
    assert rendered == {
        "ix_pae_actor_time": "CREATE INDEX ix_pae_actor_time ON project_activity_events (actor_id, occurred_at DESC, id DESC)",
        "ix_mtc_assignee": "CREATE INDEX ix_mtc_assignee ON managed_test_cases (assignee_id)",
    }
