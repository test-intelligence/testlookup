"""Schema contract for durable semantic reindex checkpoints."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _migration():
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "0164_semantic_reindex_jobs.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0164", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_is_the_single_head_successor():
    migration = _migration()
    assert migration.revision == "0164"
    assert migration.down_revision == "0163"


def test_model_exposes_durable_fenced_checkpoint_contract():
    from app.models.postgres import SemanticReindexJob, TestCase

    columns = SemanticReindexJob.__table__.columns
    assert {
        "scope_key",
        "job_id",
        "state_version",
        "status",
        "high_water_created_at",
        "high_water_id",
        "cursor_created_at",
        "cursor_id",
        "processed_count",
        "lease_owner",
        "lease_expires_at",
        "fence_token",
    } <= set(columns.keys())
    assert SemanticReindexJob.__table__.primary_key.columns.keys() == ["scope_key"]
    index_columns = {
        tuple(column.name for column in index.columns)
        for index in TestCase.__table__.indexes
    }
    assert ("created_at", "id") in index_columns
