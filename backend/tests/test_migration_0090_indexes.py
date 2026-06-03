"""Tests for migration 0090 — perf indexes flagged by the Phase AUTO loop.

Pins the revision chain, the index spec, the CONCURRENTLY/IF [NOT] EXISTS
safety, and that the ORM declares the same indexes so model + schema stay in
sync. The migration's actual DDL is exercised by ``alembic upgrade head`` in the
integration (real-Postgres) environment — this file does NOT execute DDL.
(Mirrors tests/test_migration_0082_indexes.py.)
"""
from __future__ import annotations

import importlib
import inspect

import pytest

pytest.importorskip("sqlalchemy")

_MOD = "migrations.versions.0090_perf_indexes_stage_status_run_suite"


def test_revision_chain_is_linear_to_0089():
    pytest.importorskip("alembic")  # migration imports `from alembic import op`
    mod = importlib.import_module(_MOD)
    assert mod.revision == "0090"
    assert mod.down_revision == "0089"


def test_index_specs():
    pytest.importorskip("alembic")
    mod = importlib.import_module(_MOD)
    by_name = {name: (table, cols) for name, table, cols in mod._INDEX_SPECS}
    assert by_name["ix_agent_stage_results_stage_status"] == (
        "agent_stage_results", ["stage_name", "status"],
    )
    assert by_name["ix_test_cases_run_suite"] == (
        "test_cases", ["test_run_id", "suite_name"],
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
    indexes (and the pre-existing pipeline_run_id one) so a future
    autogenerate / reader sees the real schema."""
    import os
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
    from app.models.postgres import AgentStageResult, TestCase

    def _index_names(model):
        return {ix.name for ix in model.__table__.indexes}

    stage_idx = _index_names(AgentStageResult)
    assert "ix_agent_stage_results_stage_status" in stage_idx
    assert "ix_stage_results_pipeline" in stage_idx  # from migration 0004

    tc_idx = _index_names(TestCase)
    assert "ix_test_cases_run_suite" in tc_idx
    assert "ix_test_cases_run_status" in tc_idx  # the sibling it mirrors
