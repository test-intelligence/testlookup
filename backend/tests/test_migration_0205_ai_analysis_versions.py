"""Migration 0205: every AI analysis of a test result is kept as a version (AIH-01).

Pins the shape without a database: the revision chain, the table's columns,
types, nullability and defaults, the five pinned constraint/index names and
their predicates, the foreign keys' ON DELETE, the 256-chunk backfill (and its
SQL hash being the app's constant), a downgrade that undoes exactly the
upgrade, and an ORM that declares the same. The real DDL, the backfill against
seeded rows and the SQL/Python hash parity run against Postgres in
``tests/integration/test_ai_analysis_versions_postgres.py``.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.postgres import AIAnalysisVersion
from app.services.ai_history_core import ANALYSIS_CONTENT_SHA256_SQL

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0205_ai_analysis_versions.py"

#: column -> (type, nullable)
COLUMNS = {
    "id": (postgresql.UUID, False),
    "test_case_id": (postgresql.UUID, False),
    "test_run_id": (postgresql.UUID, False),
    "project_id": (postgresql.UUID, False),
    "test_fingerprint": (sa.String, False),
    "version": (sa.Integer, False),
    "trigger": (sa.String, False),
    "pipeline_run_id": (postgresql.UUID, True),
    "actor_user_id": (postgresql.UUID, True),
    "content_sha256": (sa.String, False),
    "failure_category": (sa.String, True),
    "confidence_score": (sa.Integer, True),
    "root_cause_summary": (sa.Text, True),
    "is_flaky": (sa.Boolean, False),
    "backend_error_found": (sa.Boolean, False),
    "pod_issue_found": (sa.Boolean, False),
    "requires_human_review": (sa.Boolean, False),
    "recommended_actions": (sa.JSON, True),
    "evidence_references": (sa.JSON, True),
    "tools_used": (sa.JSON, True),
    "role_actions": (sa.JSON, True),
    "llm_provider": (sa.String, True),
    "llm_model": (sa.String, True),
    "analysis_mode": (sa.String, True),
    "provenance": (postgresql.JSONB, False),
    "created_at": (sa.DateTime, False),
}
LENGTHS = {"test_fingerprint": 64, "trigger": 24, "content_sha256": 64, "failure_category": 30,
           "llm_provider": 50, "llm_model": 100, "analysis_mode": 20}
FKS = {
    "test_case_id": ("test_cases.id", "CASCADE"),
    "test_run_id": ("test_runs.id", "CASCADE"),
    "project_id": ("projects.id", "CASCADE"),
    "pipeline_run_id": ("agent_pipeline_runs.id", "SET NULL"),
    "actor_user_id": ("users.id", "SET NULL"),
}


def _module():
    spec = importlib.util.spec_from_file_location("migration_0205", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ran(migration, step: str) -> MagicMock:
    migration.op = MagicMock()
    getattr(migration, step)()
    return migration.op


def _table(op) -> tuple[str, dict, list]:
    (call,) = op.create_table.call_args_list
    name, *items = call.args
    columns = {c.name: c for c in items if isinstance(c, sa.Column)}
    constraints = [c for c in items if not isinstance(c, sa.Column)]
    return name, columns, constraints


def test_it_follows_0204():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0205", "0204")


def test_the_table_has_the_contract_columns():
    name, columns, _ = _table(_ran(_module(), "upgrade"))
    assert name == "ai_analysis_versions"
    assert set(columns) == set(COLUMNS)
    for col, (kind, nullable) in COLUMNS.items():
        assert isinstance(columns[col].type, kind), col
        assert columns[col].nullable is nullable, col
    for col, length in LENGTHS.items():
        assert columns[col].type.length == length, col
    assert columns["created_at"].type.timezone is True
    assert not isinstance(columns["recommended_actions"].type, postgresql.JSONB), "JSON, not JSONB"
    for col in ("is_flaky", "backend_error_found", "pod_issue_found", "requires_human_review"):
        assert str(columns[col].server_default.arg.text) == "false", col
    assert str(columns["provenance"].server_default.arg.text) == "'{}'::jsonb"
    assert str(columns["id"].server_default.arg.text) == "gen_random_uuid()"


def test_the_foreign_keys_cascade_with_the_run_and_null_the_pipeline_and_actor():
    _, columns, _ = _table(_ran(_module(), "upgrade"))
    for col, (target, ondelete) in FKS.items():
        (fk,) = columns[col].foreign_keys
        assert (fk.target_fullname, fk.ondelete) == (target, ondelete), col


def test_the_pinned_constraints_and_indexes():
    op = _ran(_module(), "upgrade")
    _, _, constraints = _table(op)
    unique = [c for c in constraints if isinstance(c, sa.UniqueConstraint)]
    check = [c for c in constraints if isinstance(c, sa.CheckConstraint)]
    assert [(c.name, [col if isinstance(col, str) else col.name for col in c._pending_colargs]) for c in unique] == [
        ("uq_ai_analysis_versions_case_version", ["test_case_id", "version"])
    ]
    assert [(c.name, str(c.sqltext)) for c in check] == [("ck_ai_analysis_versions_version", "version >= 1")]
    indexes = {call.args[0]: call for call in op.create_index.call_args_list}
    assert set(indexes) == {
        "ix_ai_analysis_versions_run", "ix_ai_analysis_versions_pipeline", "uq_ai_analysis_versions_case_pipeline",
    }
    assert indexes["ix_ai_analysis_versions_run"].args[1:] == ("ai_analysis_versions", ["test_run_id"])
    pipeline = indexes["ix_ai_analysis_versions_pipeline"]
    assert pipeline.args[2] == ["pipeline_run_id"]
    assert str(pipeline.kwargs["postgresql_where"]) == "pipeline_run_id IS NOT NULL"
    replay = indexes["uq_ai_analysis_versions_case_pipeline"]
    assert replay.args[2] == ["test_case_id", "pipeline_run_id"] and replay.kwargs["unique"] is True
    assert str(replay.kwargs["postgresql_where"]) == "trigger = 'pipeline' AND pipeline_run_id IS NOT NULL"
    raw = [str(call.args[0]) for call in op.execute.call_args_list if isinstance(call.args[0], str)]
    assert raw == [
        "CREATE INDEX ix_ai_analysis_versions_project_fp ON ai_analysis_versions "
        "(project_id, test_fingerprint, created_at DESC)"
    ]


def test_the_backfill_is_256_idempotent_chunks_over_the_whole_id_space():
    migration = _module()
    statements = migration.backfill_statements()
    assert len(statements) == 256
    assert statements[0][1] == {"lo": "00000000-0000-0000-0000-000000000000", "hi": "01000000-0000-0000-0000-000000000000"}
    assert statements[1][1]["lo"] == statements[0][1]["hi"]
    assert statements[-1][1] == {"lo": "ff000000-0000-0000-0000-000000000000"}
    assert "a.id < CAST(:hi AS uuid)" not in statements[-1][0]
    for sql, params in statements:
        uuid.UUID(params["lo"])
        assert "ON CONFLICT (test_case_id, version) DO NOTHING" in sql
        assert "'backfill'" in sql and ANALYSIS_CONTENT_SHA256_SQL.format(a="a") in sql
    # Each chunk's upper bound is the next chunk's lower bound: no gap, no overlap.
    bounds = [p["lo"] for _, p in statements]
    assert bounds == sorted(bounds) and len(set(bounds)) == 256
    op = _ran(migration, "upgrade")
    executed = [call.args[0] for call in op.execute.call_args_list if not isinstance(call.args[0], str)]
    assert len(executed) == 256


def test_the_migration_hash_is_the_app_constant():
    assert _module().ANALYSIS_CONTENT_SHA256_SQL == ANALYSIS_CONTENT_SHA256_SQL


def test_downgrade_undoes_exactly_the_upgrade():
    op = _ran(_module(), "downgrade")
    assert [call.args[0] for call in op.drop_index.call_args_list] == [
        "uq_ai_analysis_versions_case_pipeline",
        "ix_ai_analysis_versions_pipeline",
        "ix_ai_analysis_versions_project_fp",
        "ix_ai_analysis_versions_run",
    ]
    op.drop_table.assert_called_once_with("ai_analysis_versions")
    op.create_table.assert_not_called()


def test_the_orm_declares_the_same():
    table = AIAnalysisVersion.__table__
    assert set(table.c.keys()) == set(COLUMNS)
    for col, (kind, nullable) in COLUMNS.items():
        assert isinstance(table.c[col].type, kind), col
        assert table.c[col].nullable is nullable, col
    for col, length in LENGTHS.items():
        assert table.c[col].type.length == length, col
    for col, (target, ondelete) in FKS.items():
        (fk,) = table.c[col].foreign_keys
        assert (fk.target_fullname, fk.ondelete) == (target, ondelete), col
    names = {c.name for c in table.constraints} | {i.name for i in table.indexes}
    assert {
        "uq_ai_analysis_versions_case_version", "ck_ai_analysis_versions_version", "ix_ai_analysis_versions_run",
        "ix_ai_analysis_versions_project_fp", "ix_ai_analysis_versions_pipeline", "uq_ai_analysis_versions_case_pipeline",
    } <= names
    dialect = postgresql.dialect()
    rendered = {i.name: str(sa.schema.CreateIndex(i).compile(dialect=dialect)) for i in table.indexes}
    assert rendered["ix_ai_analysis_versions_project_fp"].endswith(
        "ON ai_analysis_versions (project_id, test_fingerprint, created_at DESC)"
    )
    assert rendered["uq_ai_analysis_versions_case_pipeline"].startswith("CREATE UNIQUE INDEX")
    assert rendered["uq_ai_analysis_versions_case_pipeline"].endswith(
        "WHERE trigger = 'pipeline' AND pipeline_run_id IS NOT NULL"
    )
    assert rendered["ix_ai_analysis_versions_pipeline"].endswith("WHERE pipeline_run_id IS NOT NULL")
