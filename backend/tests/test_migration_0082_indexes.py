"""Tests for migration 0082 — FK indexes on hot paths.

Pins the migration's revision chain and the index spec so a future
contributor can't silently regress what the audit fixed. Also asserts
the ORM declares the same indexes so model + schema stay in sync.

The migration's actual SQL is exercised by ``alembic upgrade head`` in
the integration test environment — this file does NOT execute Postgres
DDL.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

pytest.importorskip("sqlalchemy")


def test_revision_chain_is_linear_to_0081():
    """Migration 0082 must descend from 0081 so the chain stays linear.
    A future migration colliding on revision="0082" would surface as an
    Alembic multi-head error."""
    mod = importlib.import_module("migrations.versions.0082_missing_fk_indexes")
    assert mod.revision == "0082"
    assert mod.down_revision == "0081"


def test_index_specs_cover_the_four_audit_findings():
    mod = importlib.import_module("migrations.versions.0082_missing_fk_indexes")

    by_name = {name: (table, cols) for name, table, cols in mod._INDEX_SPECS}

    # Each audit finding must be present.
    assert by_name["ix_history_test_case_id"]  == ("test_case_history", ["test_case_id"])
    assert by_name["ix_history_test_run_id"]   == ("test_case_history", ["test_run_id"])
    assert by_name["ix_defects_project_id"]    == ("defects",           ["project_id"])
    assert by_name["ix_quality_gates_project"] == ("quality_gates",     ["project_id"])
    assert len(mod._INDEX_SPECS) == 4


def test_upgrade_uses_concurrently_and_if_not_exists():
    """CONCURRENTLY avoids the write lock on production tables; the
    IF NOT EXISTS guard makes the migration safe to re-run after a
    partial failure (each index is in its own autocommit_block)."""
    mod = importlib.import_module("migrations.versions.0082_missing_fk_indexes")
    src = inspect.getsource(mod.upgrade)

    assert "autocommit_block" in src, "Each CONCURRENTLY index needs its own autocommit_block"
    assert "postgresql_concurrently=True" in src
    assert "if_not_exists=True" in src


def test_downgrade_is_symmetric_and_safe_to_rerun():
    mod = importlib.import_module("migrations.versions.0082_missing_fk_indexes")
    src = inspect.getsource(mod.downgrade)

    assert "autocommit_block" in src
    assert "postgresql_concurrently=True" in src
    assert "if_exists=True" in src, "downgrade must be idempotent (re-run safe)"


def test_orm_declares_the_indexes_so_schema_does_not_drift():
    """The ORM ``__table_args__`` must reference the same index names the
    migration creates, so a metadata-only smoke test (Base.metadata.create_all
    in dev) produces a schema that matches a migrated database."""
    from app.models.postgres import Defect, QualityGate, TestCaseHistory

    def index_names(model):
        return {idx.name for idx in model.__table__.indexes}

    assert "ix_history_test_case_id" in index_names(TestCaseHistory)
    assert "ix_history_test_run_id"  in index_names(TestCaseHistory)
    assert "ix_defects_project_id"   in index_names(Defect)
    assert "ix_quality_gates_project" in index_names(QualityGate)
