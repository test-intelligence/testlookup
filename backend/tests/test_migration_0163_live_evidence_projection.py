"""Schema contract for durable live evidence projection."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _migration():
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "0163_live_evidence_projection.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0163", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_is_the_single_head_successor():
    migration = _migration()
    assert migration.revision == "0163"
    assert migration.down_revision == "0162"


def test_models_expose_attempt_receipt_and_run_checkpoint_identity():
    from app.models.postgres import (
        LiveEventReceipt,
        LiveIngestionAttempt,
        LiveProjectionCheckpoint,
    )

    assert LiveIngestionAttempt.__table__.constraints
    assert LiveProjectionCheckpoint.__table__.primary_key.columns.keys() == ["run_id"]
    constraint_names = {
        constraint.name for constraint in LiveEventReceipt.__table__.constraints
    }
    assert "uq_live_event_receipt_event" in constraint_names
