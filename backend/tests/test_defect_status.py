"""The defect-status vocabulary and ``apply_status`` (W06 DEF-01, contract §1.1).

Every row of the ``apply_status`` table: same value is no change; live ->
closed stamps ``resolved_at``; closed -> the other closed keeps it (stamps a
legacy NULL); anything -> live clears it; a target outside the vocabulary
(including the legacy word ``CLOSED``) is refused and nothing changes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import defect_status as ds

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(days=3)


def _defect(status, resolved_at=None):
    return SimpleNamespace(resolution_status=status, resolved_at=resolved_at)


def test_the_vocabulary():
    assert ds.DEFECT_STATUSES == ("OPEN", "IN_PROGRESS", "RESOLVED", "INVALID")
    assert ds.ACTIVE_STATUSES == ("OPEN", "IN_PROGRESS")
    assert ds.CLOSED_STATUSES == ("RESOLVED", "INVALID")
    assert set(ds.ACTIVE_STATUSES) | set(ds.CLOSED_STATUSES) == set(ds.DEFECT_STATUSES)
    assert ds.STATUS_LABELS == {"OPEN": "Open", "IN_PROGRESS": "In progress", "RESOLVED": "Resolved", "INVALID": "Invalid"}
    assert ds.STATUS_INVALID_DETAIL == "resolution_status must be one of OPEN, IN_PROGRESS, RESOLVED, INVALID."
    assert ds.ACTIVE_QUERY_VALUE == "ACTIVE"
    assert ds.PRIORITIES == ("P0", "P1", "P2", "P3")
    assert ds.ESCAPE_RATE_MIN_SAMPLE == 5


@pytest.mark.parametrize("value,expected", [
    ("OPEN", "OPEN"), ("in_progress", "IN_PROGRESS"), ("  Resolved ", "RESOLVED"), ("invalid", "INVALID"),
])
def test_normalize_status_accepts_any_case(value, expected):
    assert ds.normalize_status(value) == expected


@pytest.mark.parametrize("value", ["CLOSED", "closed", "DONE", "", "IN PROGRESS", "ACTIVE", None, 3])
def test_normalize_status_refuses_everything_else_with_one_text(value):
    with pytest.raises(ValueError) as err:
        ds.normalize_status(value)
    assert str(err.value) == ds.STATUS_INVALID_DETAIL


@pytest.mark.parametrize("status,active", [
    ("OPEN", True), ("open", True), ("IN_PROGRESS", True), ("RESOLVED", False), ("INVALID", False),
    ("CLOSED", False), (None, False), ("", False),
])
def test_is_active(status, active):
    assert ds.is_active(status) is active


@pytest.mark.parametrize("status", ds.DEFECT_STATUSES)
def test_same_value_changes_nothing(status):
    defect = _defect(status, EARLIER if status in ds.CLOSED_STATUSES else None)
    before = dict(vars(defect))
    assert ds.apply_status(defect, status.lower(), now=NOW) is False
    assert vars(defect) == before


@pytest.mark.parametrize("current", ["OPEN", "IN_PROGRESS"])
@pytest.mark.parametrize("target", ["RESOLVED", "INVALID"])
def test_live_to_closed_stamps_resolved_at(current, target):
    defect = _defect(current, None)
    assert ds.apply_status(defect, target, now=NOW) is True
    assert (defect.resolution_status, defect.resolved_at) == (target, NOW)


def test_live_to_closed_overwrites_a_stale_resolved_at_on_a_live_row():
    # A legacy ACTIVE row that still carries a resolved_at: closing it now is closing it now.
    defect = _defect("OPEN", EARLIER)
    assert ds.apply_status(defect, "RESOLVED", now=NOW) is True
    assert defect.resolved_at == NOW


@pytest.mark.parametrize("current,target", [("RESOLVED", "INVALID"), ("INVALID", "RESOLVED"), ("CLOSED", "RESOLVED")])
def test_closed_to_other_closed_keeps_resolved_at(current, target):
    defect = _defect(current, EARLIER)
    assert ds.apply_status(defect, target, now=NOW) is True
    assert (defect.resolution_status, defect.resolved_at) == (target, EARLIER)


@pytest.mark.parametrize("current,target", [("RESOLVED", "INVALID"), ("INVALID", "RESOLVED")])
def test_closed_to_other_closed_stamps_a_legacy_null(current, target):
    defect = _defect(current, None)
    assert ds.apply_status(defect, target, now=NOW) is True
    assert defect.resolved_at == NOW


@pytest.mark.parametrize("current,resolved_at", [
    ("RESOLVED", EARLIER), ("INVALID", EARLIER), ("INVALID", None), ("CLOSED", EARLIER),
    ("OPEN", None), ("IN_PROGRESS", None),
])
@pytest.mark.parametrize("target", ["OPEN", "IN_PROGRESS"])
def test_anything_to_live_clears_resolved_at(current, resolved_at, target):
    defect = _defect(current, resolved_at)
    if current == target:
        pytest.skip("same value: covered above")
    assert ds.apply_status(defect, target, now=NOW) is True
    assert (defect.resolution_status, defect.resolved_at) == (target, None)


def test_an_unknown_stored_word_is_treated_as_live():
    defect = _defect("TRIAGED", EARLIER)
    assert ds.apply_status(defect, "RESOLVED", now=NOW) is True
    assert defect.resolved_at == NOW


@pytest.mark.parametrize("target", ["CLOSED", "DONE", "", "ACTIVE"])
def test_a_target_outside_the_vocabulary_is_refused_and_nothing_changes(target):
    defect = _defect("OPEN", None)
    with pytest.raises(ValueError) as err:
        ds.apply_status(defect, target, now=NOW)
    assert str(err.value) == ds.STATUS_INVALID_DETAIL
    assert (defect.resolution_status, defect.resolved_at) == ("OPEN", None)


@pytest.mark.parametrize("value,key", [
    ("ABC-123", "ABC-123"), (" abc-123 ", "ABC-123"), ("QA_2-7", "QA_2-7"),
    ("abc-1x", None), ("A-1", None), ("1AB-2", None), ("ABC123", None), ("", None),
    ("A" * 48 + "-1", "A" * 48 + "-1"), ("A" * 49 + "-1", None), (None, None),
])
def test_parse_jira_key(value, key):
    assert ds.parse_jira_key(value) == key
