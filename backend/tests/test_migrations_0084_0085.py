"""Tests for migrations 0084 (AIEvalRun JSONB) and 0085 (RunDiff index).

Pins the revision chain, ORM-vs-schema consistency, and CONCURRENTLY
usage for the audit's P3-2 and P3-4 fixes.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

pytest.importorskip("sqlalchemy")


# ── 0084: AIEvalRun.item_results JSON → JSONB ────────────────────────────────


def test_0084_revision_chain_descends_from_0083():
    mod = importlib.import_module(
        "migrations.versions.0084_ai_eval_run_item_results_jsonb"
    )
    assert mod.revision == "0084"
    assert mod.down_revision == "0083"


def test_0084_upgrade_uses_jsonb_cast():
    mod = importlib.import_module(
        "migrations.versions.0084_ai_eval_run_item_results_jsonb"
    )
    src = inspect.getsource(mod.upgrade)
    assert "ALTER COLUMN item_results TYPE JSONB" in src
    assert "USING item_results::jsonb" in src


def test_0084_downgrade_is_symmetric():
    mod = importlib.import_module(
        "migrations.versions.0084_ai_eval_run_item_results_jsonb"
    )
    src = inspect.getsource(mod.downgrade)
    assert "ALTER COLUMN item_results TYPE JSON" in src
    assert "USING item_results::json" in src


def test_0084_orm_column_type_is_jsonb():
    """The ORM column type must agree with the migrated schema."""
    from sqlalchemy.dialects.postgresql import JSONB

    from app.models.postgres import AIEvalRun

    col = AIEvalRun.__table__.c.item_results
    assert isinstance(col.type, JSONB), (
        f"AIEvalRun.item_results column type is {type(col.type).__name__}, "
        "expected JSONB (migration 0084)."
    )


# ── 0085: RunDiff.baseline_run_id index ──────────────────────────────────────


def test_0085_revision_chain_descends_from_0084():
    mod = importlib.import_module(
        "migrations.versions.0085_run_diffs_baseline_index"
    )
    assert mod.revision == "0085"
    assert mod.down_revision == "0084"


def test_0085_upgrade_uses_concurrently():
    mod = importlib.import_module(
        "migrations.versions.0085_run_diffs_baseline_index"
    )
    src = inspect.getsource(mod.upgrade)
    assert "autocommit_block" in src
    assert "postgresql_concurrently=True" in src
    assert "if_not_exists=True" in src


def test_0085_downgrade_uses_concurrently():
    mod = importlib.import_module(
        "migrations.versions.0085_run_diffs_baseline_index"
    )
    src = inspect.getsource(mod.downgrade)
    assert "autocommit_block" in src
    assert "postgresql_concurrently=True" in src
    assert "if_exists=True" in src


def test_0085_orm_declares_the_index():
    """The ORM ``__table_args__`` must reference the same index name
    the migration creates."""
    from app.models.postgres import RunDiff

    index_names = {idx.name for idx in RunDiff.__table__.indexes}
    assert "ix_run_diffs_baseline_run_id" in index_names


def test_run_baselines_baseline_index_still_present():
    """``RunBaseline.baseline_run_id`` already had ``ix_run_baselines_baseline``
    pre-audit (migration 0073) — pin it so a future refactor doesn't
    accidentally drop it while reasoning about the P3-4 fix."""
    from app.models.postgres import RunBaseline

    index_names = {idx.name for idx in RunBaseline.__table__.indexes}
    assert "ix_run_baselines_baseline" in index_names
