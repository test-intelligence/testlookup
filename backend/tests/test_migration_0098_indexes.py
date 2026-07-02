"""Tests for migration 0098 — release-gate + flaky-count perf indexes.

Pins the revision chain, the index spec, the CONCURRENTLY/IF [NOT] EXISTS
safety, and that the ORM declares the same indexes so model + schema stay in
sync. The migration's actual DDL is exercised by ``alembic upgrade head`` in the
integration (real-Postgres) environment — this file does NOT execute DDL.
(Mirrors tests/test_migration_0090_indexes.py.)
"""
from __future__ import annotations

import importlib
import inspect

import pytest

pytest.importorskip("sqlalchemy")

_MOD = "migrations.versions.0098_perf_indexes_defects_history"


def test_revision_chain_is_linear_to_0097():
    pytest.importorskip("alembic")  # migration imports `from alembic import op`
    mod = importlib.import_module(_MOD)
    assert mod.revision == "0098"
    assert mod.down_revision == "0097"


def test_index_specs():
    pytest.importorskip("alembic")
    mod = importlib.import_module(_MOD)
    by_name = {name: (table, cols) for name, table, cols in mod._INDEX_SPECS}
    assert by_name["ix_defects_project_status_severity"] == (
        "defects", ["project_id", "resolution_status", "severity"],
    )
    assert by_name["ix_history_fingerprint_date_status"] == (
        "test_case_history", ["test_fingerprint", "created_at", "status"],
    )
    assert len(mod._INDEX_SPECS) == 2


def test_upgrade_uses_concurrently_and_if_not_exists():
    pytest.importorskip("alembic")
    mod = importlib.import_module(_MOD)
    src = inspect.getsource(mod.upgrade)
    assert "autocommit_block" in src  # CONCURRENTLY can't run in a transaction
    assert "postgresql_concurrently=True" in src
    assert "if_not_exists=True" in src


def test_downgrade_is_symmetric_and_safe_to_rerun():
    pytest.importorskip("alembic")
    mod = importlib.import_module(_MOD)
    src = inspect.getsource(mod.downgrade)
    assert "autocommit_block" in src
    assert "postgresql_concurrently=True" in src
    assert "if_exists=True" in src


def test_orm_declares_the_same_indexes():
    """Model + schema parity: the ORM __table_args__ must declare both new
    indexes (alongside the pre-existing siblings) so a future autogenerate /
    reader sees the real schema."""
    import os
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
    from app.models.postgres import Defect, TestCaseHistory

    def _index_names(model):
        return {ix.name for ix in model.__table__.indexes}

    defect_idx = _index_names(Defect)
    assert "ix_defects_project_status_severity" in defect_idx
    assert "ix_defects_project_id" in defect_idx  # single-col sibling (migration 0082)

    hist_idx = _index_names(TestCaseHistory)
    assert "ix_history_fingerprint_date_status" in hist_idx
    assert "ix_history_fingerprint_date" in hist_idx  # 2-col sibling it extends
