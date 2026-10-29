"""Migrations 0243 / 0244 (W23a FAIL-11 / TRND-03): shape only, no database.

* the chain 0242 -> 0243 -> 0244, with 0244 the only head (every earlier
  migration test asserts ``heads[0] >= …`` and keeps passing);
* the CHECK literal lists are the vocabularies (``insight_vocab`` /
  ``run_cadence_vocab``): a migration must not import application code, so
  the copy is pinned here;
* 0243 seeds ``cross_run_insights`` ON (``_FLAGS``, ``INSERT_SQL`` with
  ``true`` and ``ON CONFLICT (key) DO NOTHING``) and its downgrade deletes the
  row only while it still equals the seed;
* each downgrade undoes exactly what its upgrade built, in reverse order;
* every ``create_index`` names a table the same migration creates (the
  concurrent-index guard's own reasoning, asserted locally); neither file
  imports ``app`` or names ``project_retention_policies``;
* the ORM is in step with the DDL (columns, nullability, server defaults,
  CHECK texts, FKs, the partial unique indexes' ``postgresql_where`` and
  their ``sqlite_where`` twins).

The real DDL runs against Postgres in
``tests/integration/test_migrations_0243_0244_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from unittest.mock import MagicMock

from app.services import insight_vocab as iv
from app.services import run_cadence_vocab as cv

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0243 = VERSIONS / "0243_cross_run_insights.py"
M0244 = VERSIONS / "0244_run_cadences.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
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
            out[str(values["revision"])] = values.get("down_revision")  # type: ignore[assignment]
    return out


def _parts(call) -> tuple[dict, dict]:
    """(columns by name, CHECK texts by name) of one ``op.create_table`` call."""
    items = call.args[1:]
    columns = {c.name: c for c in items if c.__class__.__name__ == "Column"}
    checks = {c.name: str(c.sqltext) for c in items if c.__class__.__name__ == "CheckConstraint"}
    return columns, checks


def _tables(mod) -> dict:
    return {c.args[0]: c for c in mod.op.create_table.call_args_list}


# ── the chain and the hygiene ────────────────────────────────────────────────

def test_the_chain_is_linear_with_0244_the_only_head():
    revisions = _revisions()
    assert revisions["0243"] == "0242" and revisions["0244"] == "0243"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0242") == 1 and parents.count("0243") == 1   # no fork at or below
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0244"


def test_neither_migration_imports_the_application_or_touches_retention_policies():
    for path in (M0243, M0244):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        modules = [
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        ] + [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not [m for m in modules if m == "app" or m.startswith("app.")], (path.name, modules)
        assert "project_retention_policies" not in source, path.name


def test_every_index_is_built_on_a_table_the_same_migration_creates():
    for path, name in ((M0243, "m0243_idx"), (M0244, "m0244_idx")):
        mod = _load(path, name)
        mod.upgrade()
        created = set(_tables(mod))
        indexed = [c.args[1] for c in mod.op.create_index.call_args_list]
        assert indexed and set(indexed) <= created, (path.name, indexed, created)
        # No raw SQL index build and no change to an existing table.
        source = path.read_text(encoding="utf-8").upper()
        assert "CREATE INDEX" not in source and "ALTER TABLE" not in source
        assert not mod.op.add_column.called and not mod.op.alter_column.called


# ── 0243: the insight tables and the flag ────────────────────────────────────

def test_0243_check_literals_are_the_vocabulary():
    mod = _load(M0243, "m0243_vocab")
    assert mod._KINDS == iv.INSIGHT_KINDS
    assert mod._SEVERITIES == iv.INSIGHT_SEVERITIES
    assert mod._REASONS == iv.INSIGHT_RESOLVE_REASONS
    mod.upgrade()
    tables = _tables(mod)
    _, insight_checks = _parts(tables["project_insights"])
    _, state_checks = _parts(tables["insight_detector_states"])

    def literals(text: str) -> tuple[str, ...]:
        return tuple(re.findall(r"'([^']+)'", text))

    assert literals(insight_checks["ck_project_insights_kind"]) == iv.INSIGHT_KINDS
    assert literals(insight_checks["ck_project_insights_severity"]) == iv.INSIGHT_SEVERITIES
    assert literals(insight_checks["ck_project_insights_resolved_reason"]) == iv.INSIGHT_RESOLVE_REASONS
    assert literals(state_checks["ck_insight_detector_states_kind"]) == iv.INSIGHT_KINDS


def test_0243_upgrade_shape():
    mod = _load(M0243, "m0243_up")
    assert (mod.revision, mod.down_revision) == ("0243", "0242")
    mod.upgrade()
    assert [c[0] for c in mod.op.method_calls] == [
        "create_table", "create_index", "create_index", "create_table", "execute",
    ]
    tables = _tables(mod)
    columns, checks = _parts(tables["project_insights"])
    assert list(columns) == [
        "id", "project_id", "kind", "subject_key", "subject_label", "window_key", "severity", "evidence",
        "first_seen_at", "last_seen_at", "detection_count", "resolved_at", "resolved_reason",
        "dismissed_at", "dismissed_by_id", "dismissed_by_name",
    ]
    assert set(checks) == {
        "ck_project_insights_kind", "ck_project_insights_severity",
        "ck_project_insights_resolved_reason", "ck_project_insights_resolved_pair",
    }
    assert checks["ck_project_insights_resolved_pair"] == "(resolved_at IS NULL) = (resolved_reason IS NULL)"
    project_fk = next(iter(columns["project_id"].foreign_keys))
    assert project_fk.target_fullname == "projects.id" and project_fk.ondelete == "CASCADE"
    user_fk = next(iter(columns["dismissed_by_id"].foreign_keys))
    assert user_fk.target_fullname == "users.id" and user_fk.ondelete == "SET NULL"
    assert str(columns["detection_count"].server_default.arg) == "1"
    assert str(columns["first_seen_at"].server_default.arg) == "now()"
    assert columns["last_seen_at"].server_default is None and not columns["last_seen_at"].nullable
    indexes = [(c.args[0], c.args[1], c.args[2], c.kwargs) for c in mod.op.create_index.call_args_list]
    assert [i[:3] for i in indexes] == [
        ("uq_project_insights_open", "project_insights", ["project_id", "kind", "subject_key", "window_key"]),
        ("ix_project_insights_project_last_seen", "project_insights", ["project_id", "last_seen_at"]),
    ]
    assert indexes[0][3]["unique"] is True
    assert str(indexes[0][3]["postgresql_where"]) == "resolved_at IS NULL"
    state_columns, _ = _parts(tables["insight_detector_states"])
    assert [n for n, c in state_columns.items() if c.primary_key] == ["project_id", "kind"]
    assert str(state_columns["subjects_measured"].server_default.arg) == "0"
    assert str(state_columns["firing"].server_default.arg) == "0"


def test_0243_seeds_the_flag_on_and_leaves_an_existing_row_alone():
    mod = _load(M0243, "m0243_flag")
    assert [key for key, _ in mod._FLAGS] == [iv.INSIGHT_FLAG] == ["cross_run_insights"]
    sql = " ".join(mod.INSERT_SQL.split())
    assert "VALUES (gen_random_uuid(), :key, :description, true, 100, now(), now())" in sql
    assert sql.endswith("ON CONFLICT (key) DO NOTHING")
    assert "NOT EXISTS" not in sql            # ON on every install, new or upgraded (D-4)
    delete = " ".join(mod.DELETE_SQL.split())
    for clause in ("key = :key", "description = :description", "created_at = updated_at",
                   "enabled_projects IS NULL", "enabled_roles IS NULL", "rollout_percent = 100"):
        assert clause in delete, clause
    mod.upgrade()
    (execute,) = mod.op.execute.call_args_list
    clause = execute.args[0]
    assert str(clause) == mod.INSERT_SQL
    assert clause.compile().params == {"key": "cross_run_insights", "description": mod._FLAGS[0][1]}


def test_0243_downgrade_undoes_the_upgrade_in_reverse():
    mod = _load(M0243, "m0243_down")
    mod.downgrade()
    assert [c[0] for c in mod.op.method_calls] == ["execute", "drop_table", "drop_index", "drop_index", "drop_table"]
    (execute,) = mod.op.execute.call_args_list
    assert str(execute.args[0]) == mod.DELETE_SQL
    assert [c.args[0] for c in mod.op.drop_table.call_args_list] == ["insight_detector_states", "project_insights"]
    assert [c.args[0] for c in mod.op.drop_index.call_args_list] == [
        "uq_project_insights_open", "ix_project_insights_project_last_seen",
    ]
    assert all(c.kwargs["table_name"] == "project_insights" for c in mod.op.drop_index.call_args_list)


# ── 0244: the cadence and window tables ──────────────────────────────────────

def test_0244_check_literals_are_the_vocabulary():
    mod = _load(M0244, "m0244_vocab")
    assert mod._MODES == cv.CADENCE_MODES
    assert mod._OUTCOMES == cv.WINDOW_OUTCOMES
    mod.upgrade()
    tables = _tables(mod)
    _, cadence_checks = _parts(tables["run_cadences"])
    _, window_checks = _parts(tables["run_cadence_windows"])
    assert tuple(re.findall(r"'([^']+)'", cadence_checks["ck_run_cadences_mode"])) == cv.CADENCE_MODES
    assert tuple(re.findall(r"'([^']+)'", window_checks["ck_run_cadence_windows_outcome"])) == cv.WINDOW_OUTCOMES
    assert cadence_checks["ck_run_cadences_grace"] == f"grace_minutes BETWEEN {cv.GRACE_MIN} AND {cv.GRACE_MAX}"


def test_0244_upgrade_shape():
    mod = _load(M0244, "m0244_up")
    assert (mod.revision, mod.down_revision) == ("0244", "0243")
    mod.upgrade()
    assert [c[0] for c in mod.op.method_calls] == [
        "create_table", "create_index", "create_index", "create_table", "create_index",
    ]
    tables = _tables(mod)
    columns, checks = _parts(tables["run_cadences"])
    assert list(columns) == [
        "id", "project_id", "suite_name", "suite_key", "mode", "weekdays", "expected_by", "cron", "timezone",
        "grace_minutes", "effective_from", "checked_through", "paused_at", "paused_until", "paused_reason",
        "paused_by_id", "created_by_id", "created_at", "updated_at",
    ]
    assert set(checks) == {
        "ck_run_cadences_mode", "ck_run_cadences_mode_fields", "ck_run_cadences_grace",
        "ck_run_cadences_pause_fields", "ck_run_cadences_suite_pair",
    }
    assert str(columns["grace_minutes"].server_default.arg) == "60"
    for name in ("paused_by_id", "created_by_id"):
        fk = next(iter(columns[name].foreign_keys))
        assert fk.target_fullname == "users.id" and fk.ondelete == "SET NULL"
    indexes = [(c.args[0], c.args[1], c.args[2], c.kwargs) for c in mod.op.create_index.call_args_list]
    assert [i[:3] for i in indexes] == [
        ("uq_run_cadences_project_any", "run_cadences", ["project_id"]),
        ("uq_run_cadences_project_suite", "run_cadences", ["project_id", "suite_key"]),
        ("ix_run_cadence_windows_project_deadline", "run_cadence_windows", ["project_id", "deadline"]),
    ]
    assert indexes[0][3]["unique"] is True and str(indexes[0][3]["postgresql_where"]) == "suite_key IS NULL"
    assert indexes[1][3]["unique"] is True and str(indexes[1][3]["postgresql_where"]) == "suite_key IS NOT NULL"
    window_columns, window_checks = _parts(tables["run_cadence_windows"])
    cadence_fk = next(iter(window_columns["cadence_id"].foreign_keys))
    assert cadence_fk.target_fullname == "run_cadences.id" and cadence_fk.ondelete == "CASCADE"
    assert window_checks["ck_run_cadence_windows_range"] == "window_end > window_start"
    uniques = [c for c in tables["run_cadence_windows"].args[1:] if c.__class__.__name__ == "UniqueConstraint"]
    assert [(u.name, [c for c in u._pending_colargs]) for u in uniques] == [
        ("uq_run_cadence_windows_cadence_start", ["cadence_id", "window_start"]),
    ]
    assert not [c for c in (*columns.values(), *window_columns.values()) for fk in c.foreign_keys
                if fk.target_fullname.startswith("test_runs.")]


def test_0244_downgrade_undoes_the_upgrade_in_reverse():
    mod = _load(M0244, "m0244_down")
    mod.downgrade()
    assert [c[0] for c in mod.op.method_calls] == ["drop_index", "drop_table", "drop_index", "drop_index", "drop_table"]
    assert [(c.args[0], c.kwargs["table_name"]) for c in mod.op.drop_index.call_args_list] == [
        ("ix_run_cadence_windows_project_deadline", "run_cadence_windows"),
        ("uq_run_cadences_project_suite", "run_cadences"),
        ("uq_run_cadences_project_any", "run_cadences"),
    ]
    assert [c.args[0] for c in mod.op.drop_table.call_args_list] == ["run_cadence_windows", "run_cadences"]


# ── the ORM is in step with the DDL ──────────────────────────────────────────

def _orm_checks(table) -> dict[str, str]:
    return {c.name: str(c.sqltext) for c in table.constraints if c.__class__.__name__ == "CheckConstraint"}


def _assert_orm_matches(table, create_call) -> None:
    columns, checks = _parts(create_call)
    assert {c.name for c in table.columns} == set(columns)
    assert _orm_checks(table) == checks
    for name, ddl in columns.items():
        orm = table.c[name]
        if not ddl.primary_key:
            assert orm.nullable == ddl.nullable, name
        ddl_default = None if ddl.server_default is None else str(ddl.server_default.arg)
        orm_default = None if orm.server_default is None else str(orm.server_default.arg)
        if ddl_default == "now()":
            assert orm_default is not None and "now" in orm_default, name
        else:
            assert orm_default == ddl_default, name
        assert {(fk.target_fullname, fk.ondelete) for fk in orm.foreign_keys} == {
            (fk.target_fullname, fk.ondelete) for fk in ddl.foreign_keys
        }, name


def _orm_indexes(table) -> dict[str, tuple[list[str], bool, str | None, str | None]]:
    return {
        ix.name: (
            [c.name for c in ix.columns],
            bool(ix.unique),
            None if ix.dialect_options["postgresql"]["where"] is None else str(ix.dialect_options["postgresql"]["where"]),
            None if ix.dialect_options["sqlite"]["where"] is None else str(ix.dialect_options["sqlite"]["where"]),
        )
        for ix in table.indexes
    }


def test_the_orm_matches_0243():
    from app.models.postgres import InsightDetectorState, ProjectInsight

    mod = _load(M0243, "m0243_orm")
    mod.upgrade()
    tables = _tables(mod)
    _assert_orm_matches(ProjectInsight.__table__, tables["project_insights"])
    _assert_orm_matches(InsightDetectorState.__table__, tables["insight_detector_states"])
    assert _orm_indexes(ProjectInsight.__table__) == {
        "uq_project_insights_open": (
            ["project_id", "kind", "subject_key", "window_key"], True, "resolved_at IS NULL", "resolved_at IS NULL",
        ),
        "ix_project_insights_project_last_seen": (["project_id", "last_seen_at"], False, None, None),
    }
    assert [c.name for c in InsightDetectorState.__table__.primary_key.columns] == ["project_id", "kind"]


def test_the_orm_matches_0244():
    from app.models.postgres import RunCadence, RunCadenceWindow

    mod = _load(M0244, "m0244_orm")
    mod.upgrade()
    tables = _tables(mod)
    _assert_orm_matches(RunCadence.__table__, tables["run_cadences"])
    _assert_orm_matches(RunCadenceWindow.__table__, tables["run_cadence_windows"])
    assert _orm_indexes(RunCadence.__table__) == {
        "uq_run_cadences_project_any": (["project_id"], True, "suite_key IS NULL", "suite_key IS NULL"),
        "uq_run_cadences_project_suite": (
            ["project_id", "suite_key"], True, "suite_key IS NOT NULL", "suite_key IS NOT NULL",
        ),
    }
    assert _orm_indexes(RunCadenceWindow.__table__) == {
        "ix_run_cadence_windows_project_deadline": (["project_id", "deadline"], False, None, None),
    }
    uniques = {
        c.name: [col.name for col in c.columns]
        for c in RunCadenceWindow.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    }
    assert uniques == {"uq_run_cadence_windows_cadence_start": ["cadence_id", "window_start"]}


def test_the_run_missed_event_is_the_vocabulary_value():
    from app.models.postgres import NotificationEventType

    assert NotificationEventType.RUN_MISSED.value == cv.RUN_MISSED_EVENT == "run.missed"
    assert NotificationEventType("run.missed") is NotificationEventType.RUN_MISSED
