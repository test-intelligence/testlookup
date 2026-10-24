"""Migrations 0226 and 0227 (W12 SRCH-02 / SRCH-05): shape only, no database.

* 0226: two nullable columns on ``semantic_reindex_jobs`` (``collection``
  ``VARCHAR(63)``, ``embedder_key`` ``VARCHAR(200)``) and the backfill of
  every existing row to the legacy generation (``default`` /
  ``test_case_search``).
* 0227: the ``search_query_events`` table -- with NO column that can hold the
  query text and no user id -- its non-negative check and two indexes (plain
  builds: the table is created in the same file).

Pinned here: the revisions and docstrings; the chain 0225 -> 0226 -> 0227,
linear, 0227 the only head; no application import; the concurrent-index
guard's own scanner sees only builds on the table 0227 creates; the upgrades'
operations in order; each downgrade is its upgrade's exact reverse; the ORM
declares the same names, types, nullability, defaults and index names. The
PostgreSQL behaviour runs in ``tests/integration/test_migration_0226_0227_postgres.py``.
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
M0226 = VERSIONS / "0226_semantic_reindex_job_generation.py"
M0227 = VERSIONS / "0227_search_query_events.py"
EVENT_COLUMNS = [
    "id", "project_id", "q_hash", "q_length", "word_count", "filter_count", "entity_scope",
    "mode_requested", "mode_used", "fallback_reason", "result_count", "counts_exact", "latency_ms",
    "created_at",
]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(path: Path, step: str) -> MagicMock:
    module = _load(path, f"{path.stem}_{step}")
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


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }


# ── revisions, chain, imports ────────────────────────────────────────────────


def test_the_revisions_and_their_docstrings():
    m26, m27 = _load(M0226, "m0226"), _load(M0227, "m0227")
    assert (m26.revision, m26.down_revision) == ("0226", "0225")
    assert (m27.revision, m27.down_revision) == ("0227", "0226")
    assert "Revision ID: 0226" in m26.__doc__ and "Revises: 0225" in m26.__doc__
    assert "Revision ID: 0227" in m27.__doc__ and "Revises: 0226" in m27.__doc__
    assert "NO query text" in m27.__doc__ and "30 days" in m27.__doc__


def test_the_chain_is_linear_with_0227_the_only_head():
    revisions = _revisions()
    assert revisions["0226"] == "0225" and revisions["0227"] == "0226"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0225") == 1, "nothing else forks off 0225"
    assert parents.count("0226") == 1, "nothing else forks off 0226"
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0227"


def test_they_import_no_application_code():
    for path in (M0226, M0227):
        imported = _imports(path)
        assert not any(name.startswith("app") for name in imported), (path.name, imported)


# ── the concurrent-index guard's view ────────────────────────────────────────


def test_0226_builds_no_index():
    source = M0226.read_text(encoding="utf-8")
    assert _blocking(source) == []
    assert _concurrent_outside_autocommit(source) == []
    assert list(_operations(source)) == []


def test_0227_builds_only_on_the_table_it_creates():
    source = M0227.read_text(encoding="utf-8")
    assert _blocking(source) == []
    assert _concurrent_outside_autocommit(source) == []
    seen = sorted((op.kind, op.table) for op in _operations(source))
    assert seen == [
        ("drop", "search_query_events"), ("drop", "search_query_events"),
        ("index", "search_query_events"), ("index", "search_query_events"),
    ]


# ── 0226 ─────────────────────────────────────────────────────────────────────


def test_0226_upgrade_adds_two_nullable_columns_then_backfills_the_legacy_generation():
    op = _run(M0226, "upgrade")
    assert [c[0] for c in op.method_calls] == ["add_column", "add_column", "execute"]
    added = [(c.args[0], c.args[1]) for c in op.add_column.call_args_list]
    assert [(t, col.name) for t, col in added] == [
        ("semantic_reindex_jobs", "collection"), ("semantic_reindex_jobs", "embedder_key"),
    ]
    collection, embedder = (col for _, col in added)
    assert isinstance(collection.type, sa.String) and collection.type.length == 63
    assert isinstance(embedder.type, sa.String) and embedder.type.length == 200
    for column in (collection, embedder):
        assert column.nullable is True and column.server_default is None
        assert not column.index and not column.unique and not column.foreign_keys
    (sql,) = op.execute.call_args.args
    assert " ".join(sql.split()) == (
        "UPDATE semantic_reindex_jobs SET embedder_key = 'default', "
        "collection = 'test_case_search' WHERE embedder_key IS NULL"
    )


def test_0226_downgrade_is_the_exact_reverse():
    op = _run(M0226, "downgrade")
    assert [(c[0], c.args) for c in op.method_calls] == [
        ("drop_column", ("semantic_reindex_jobs", "embedder_key")),
        ("drop_column", ("semantic_reindex_jobs", "collection")),
    ]


def test_0226_the_orm_declares_the_same_columns():
    from app.models.postgres import SemanticReindexJob

    op = _run(M0226, "upgrade")
    table = SemanticReindexJob.__table__
    for _, column in (c.args for c in op.add_column.call_args_list):
        declared = table.c[column.name]
        assert declared.nullable is True, column.name
        assert isinstance(declared.type, sa.String) and declared.type.length == column.type.length
        assert declared.server_default is None and declared.default is None, column.name
    # Placed after fence_token (the contract's §1.2): the hand-built rows in
    # the tests never name them, and both stay None there.
    names = [c.name for c in table.columns]
    assert names.index("collection") == names.index("fence_token") + 1
    assert names.index("embedder_key") == names.index("collection") + 1


# ── 0227 ─────────────────────────────────────────────────────────────────────


def test_0227_upgrade_creates_the_table_then_the_two_indexes():
    op = _run(M0227, "upgrade")
    assert [c[0] for c in op.method_calls] == ["create_table", "create_index", "create_index"]

    call = op.create_table.call_args
    assert call.args[0] == "search_query_events"
    items = call.args[1:]
    columns = {c.name: c for c in items if isinstance(c, sa.Column)}
    checks = [c for c in items if isinstance(c, sa.CheckConstraint)]
    assert list(columns) == EVENT_COLUMNS
    assert columns["id"].primary_key and isinstance(columns["id"].type, sa.Uuid)
    (fk,) = columns["project_id"].foreign_keys
    assert fk.target_fullname == "projects.id" and fk.ondelete == "CASCADE"
    assert columns["project_id"].nullable is True
    assert columns["q_hash"].type.length == 64 and columns["q_hash"].nullable is False
    for name in ("q_length", "word_count", "filter_count"):
        assert isinstance(columns[name].type, sa.SmallInteger) and columns[name].nullable is False, name
    assert columns["word_count"].server_default.arg == "0"
    assert columns["filter_count"].server_default.arg == "0"
    assert columns["q_length"].server_default is None
    assert (columns["entity_scope"].type.length, columns["mode_requested"].type.length,
            columns["mode_used"].type.length, columns["fallback_reason"].type.length) == (20, 10, 10, 30)
    assert columns["fallback_reason"].nullable is True
    for name in ("entity_scope", "mode_requested", "mode_used", "result_count", "latency_ms", "counts_exact"):
        assert columns[name].nullable is False, name
    assert isinstance(columns["result_count"].type, sa.Integer)
    assert isinstance(columns["latency_ms"].type, sa.Integer)
    assert str(columns["counts_exact"].server_default.arg) == "true"
    assert columns["created_at"].type.timezone is True and columns["created_at"].nullable is False
    assert str(columns["created_at"].server_default.arg) == "now()"

    (check,) = checks
    assert check.name == "ck_search_query_events_nonnegative"
    assert " ".join(str(check.sqltext).split()) == (
        "q_length >= 0 AND word_count >= 0 AND filter_count >= 0 AND result_count >= 0 AND latency_ms >= 0"
    )

    first, second = op.create_index.call_args_list
    assert first.args[:2] == ("ix_search_query_events_project_created", "search_query_events")
    assert [str(c) for c in first.args[2]] == ["project_id", "created_at DESC"]
    assert second.args == ("ix_search_query_events_created", "search_query_events", ["created_at"])
    for index in (first, second):
        assert not index.kwargs.get("unique") and not index.kwargs.get("postgresql_concurrently")


def test_0227_no_column_can_hold_the_query_text_or_a_person():
    """The no-text rule, at the shape: the only string columns are the hash
    (64 hex) and three closed vocabularies; no ``q`` / ``query`` / ``text``
    column, no user id, no IP."""
    op = _run(M0227, "upgrade")
    columns = {c.name: c for c in op.create_table.call_args.args[1:] if isinstance(c, sa.Column)}
    assert not {"q", "query", "text", "query_text", "user_id", "ip", "ip_address"} & set(columns)
    strings = {name: c.type.length for name, c in columns.items() if isinstance(c.type, sa.String)}
    assert strings == {"q_hash": 64, "entity_scope": 20, "mode_requested": 10, "mode_used": 10, "fallback_reason": 30}
    assert not any(isinstance(c.type, (sa.Text, sa.JSON)) for c in columns.values())


def test_0227_downgrade_is_the_exact_reverse():
    op = _run(M0227, "downgrade")
    assert [(c[0], c.args) for c in op.method_calls] == [
        ("drop_index", ("ix_search_query_events_created",)),
        ("drop_index", ("ix_search_query_events_project_created",)),
        ("drop_table", ("search_query_events",)),
    ]
    for call in op.drop_index.call_args_list:
        assert call.kwargs == {"table_name": "search_query_events"}


def test_0227_the_orm_declares_the_same_names_types_and_defaults():
    from app.models.postgres import SearchQueryEvent

    op = _run(M0227, "upgrade")
    migrated = {c.name: c for c in op.create_table.call_args.args[1:] if isinstance(c, sa.Column)}
    table = SearchQueryEvent.__table__
    assert table.name == "search_query_events"
    assert [c.name for c in table.columns] == EVENT_COLUMNS
    for name, column in migrated.items():
        declared = table.c[name]
        assert declared.nullable is column.nullable, name
        assert declared.primary_key is column.primary_key, name
        assert type(declared.type)._type_affinity is type(column.type)._type_affinity, name
        if getattr(column.type, "length", None):
            assert declared.type.length == column.type.length, name
        assert {(fk.target_fullname, fk.ondelete) for fk in declared.foreign_keys} == {
            (fk.target_fullname, fk.ondelete) for fk in column.foreign_keys
        }, name
        assert (declared.server_default is None) is (column.server_default is None), name
    assert table.c["counts_exact"].default.arg is True
    assert table.c["word_count"].default.arg == 0 and table.c["filter_count"].default.arg == 0

    indexes = {i.name: i for i in table.indexes}
    assert set(indexes) == {"ix_search_query_events_project_created", "ix_search_query_events_created"}
    assert [str(e) for e in indexes["ix_search_query_events_project_created"].expressions] == [
        "search_query_events.project_id", "created_at DESC",
    ]
    assert [c.name for c in indexes["ix_search_query_events_created"].columns] == ["created_at"]
    (check,) = [c for c in table.constraints if isinstance(c, sa.CheckConstraint)]
    assert check.name == "ck_search_query_events_nonnegative"
    migrated_check = next(c for c in op.create_table.call_args.args[1:] if isinstance(c, sa.CheckConstraint))
    assert " ".join(str(check.sqltext).split()) == " ".join(str(migrated_check.sqltext).split())
