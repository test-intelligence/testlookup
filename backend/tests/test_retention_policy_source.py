"""D-75: an imported project's retention row is not a customisation.

A project import writes an all-inherit ``project_retention_policies`` row (every
inheritable field NULL) carrying only ``purge_paused_until`` so the imported
history is not purged. The Retention page then read "Customized" on a project
nobody configured. ``source`` is "custom" only when the row sets a value.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import retention_service as svc
from app.services.retention_vocab import INHERITABLE_FIELDS

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _row(**values):
    base = {field: None for field in (*INHERITABLE_FIELDS, "ai_versions_per_run", "ai_versions_per_test")}
    base.update(id=uuid.uuid4(), purge_paused_until=None, updated_by_user_id=None)
    base.update(values)
    return SimpleNamespace(**base)


def test_an_imported_all_inherit_row_reads_as_default():
    row = _row(purge_paused_until=NOW + timedelta(days=90))
    eff = svc._effective_from(row, None, now=NOW)
    assert eff.source == "default"
    # The pause itself is still reported: the page explains where it came from.
    assert eff.purge_paused_until == NOW + timedelta(days=90)


def test_no_row_reads_as_default():
    assert svc._effective_from(None, None, now=NOW).source == "default"


@pytest.mark.parametrize("field, value", [
    ("enabled", False), ("runs_days", 400), ("purge_window_start_utc", 4), ("ai_versions_per_run", 5),
])
def test_a_row_that_sets_a_value_is_custom(field, value):
    assert svc._effective_from(_row(**{field: value}), None, now=NOW).source == "custom"
