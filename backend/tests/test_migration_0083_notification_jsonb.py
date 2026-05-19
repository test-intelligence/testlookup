"""Tests for migration 0083 — promote NotificationPreference.events to JSONB.

Pins the revision chain, the ORM column type, and the migration's
USING-cast text so a future contributor can't silently regress the
type promotion (e.g. revert to JSON without realising the ORM still
says JSONB).
"""
from __future__ import annotations

import importlib
import inspect

import pytest

pytest.importorskip("sqlalchemy")


def test_revision_chain_descends_from_0082():
    mod = importlib.import_module(
        "migrations.versions.0083_notification_pref_events_jsonb"
    )
    assert mod.revision == "0083"
    assert mod.down_revision == "0082"


def test_upgrade_casts_via_jsonb_to_preserve_existing_rows():
    """USING events::jsonb is the safe in-place cast — every existing
    JSON list parses as a valid JSONB list with no shape change."""
    mod = importlib.import_module(
        "migrations.versions.0083_notification_pref_events_jsonb"
    )
    src = inspect.getsource(mod.upgrade)

    assert "ALTER COLUMN events TYPE JSONB" in src
    assert "USING events::jsonb" in src


def test_downgrade_is_symmetric():
    mod = importlib.import_module(
        "migrations.versions.0083_notification_pref_events_jsonb"
    )
    src = inspect.getsource(mod.downgrade)

    assert "ALTER COLUMN events TYPE JSON" in src
    assert "USING events::json" in src


def test_orm_column_type_is_jsonb_not_json():
    """The ORM must agree with the migrated column type. Without this
    test a future ``mapped_column(JSON, ...)`` revert would compile
    cleanly but generate the wrong schema in dev-only ``create_all``."""
    from sqlalchemy.dialects.postgresql import JSONB

    from app.models.postgres import NotificationPreference

    col = NotificationPreference.__table__.c.events
    # SQLAlchemy stores the column type instance; check the dialect class.
    assert isinstance(col.type, JSONB), (
        f"NotificationPreference.events column type is {type(col.type).__name__}, "
        "expected JSONB (migration 0083). If you switched back to JSON, "
        "revert the model change to match the schema."
    )
