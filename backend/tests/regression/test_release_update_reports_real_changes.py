"""A release edit reports only the fields that really changed (W05 local walk, 2026-10-15).

Changing only the owner of a release logged "Release R changed: owner, planned_date": the row
holds an aware UTC ``planned_date`` and the edit dialog sends a bare date, which parses naive, so
the raw comparison always differed and every edit claimed a date change.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.routers.releases import _comparable


def test_a_bare_date_equals_the_stored_utc_midnight():
    stored = datetime(2026, 11, 18, tzinfo=timezone.utc)
    sent = datetime(2026, 11, 18)
    assert _comparable(stored) == _comparable(sent)


def test_a_real_date_change_still_counts():
    stored = datetime(2026, 11, 18, tzinfo=timezone.utc)
    assert _comparable(stored) != _comparable(datetime(2026, 11, 19))


def test_an_aware_value_in_another_zone_compares_by_instant():
    stored = datetime(2026, 11, 18, tzinfo=timezone.utc)
    same_instant = datetime(2026, 11, 18, 2, tzinfo=timezone(timedelta(hours=2)))
    assert _comparable(stored) == _comparable(same_instant)


def test_ids_compare_as_strings():
    value = uuid.uuid4()
    assert _comparable(value) == _comparable(str(value))
